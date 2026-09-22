import asyncio
import json
import os
from collections.abc import AsyncIterator

from fastmcp import Client

from llm_client import LlmTurnResult, stream_turn
from llm_sockets import ConnPool

# Max response.create turns per Agent.run (initial + tool follow-ups)
MAX_DEPTH = 10
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:5000/mcp")
LLM_NAME = "local_llm"
POOL_SIZE = max(1, int(os.environ.get("LINKS_POOL_SIZE", "4")))

LOAD_SKILL_TOOL = {
    "type": "function",
    "name": "load_skill",
    "description": "Load a skill by name to unlock its tools. Pass the skill name only.",
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Skill name, e.g. weather, travel, finance",
            },
        },
        "required": ["skill_name"],
    },
}


def openai_tool(tool) -> dict:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
    }


def _tool_output_str(result) -> str:
    """Normalize FastMCP CallToolResult into a string for function_call_output."""
    if result.data is not None:
        value = result.data
    elif result.structured_content is not None:
        sc = result.structured_content
        value = sc["result"] if "result" in sc else sc
    else:
        texts = [
            block.text
            for block in (result.content or [])
            if getattr(block, "text", None)
        ]
        value = "\n".join(texts)

    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _prompt_text(prompt_result) -> str:
    parts: list[str] = []
    for msg in prompt_result.messages or []:
        content = msg.content
        if hasattr(content, "text") and content.text:
            parts.append(content.text)
        elif isinstance(content, str):
            parts.append(content)
    return "\n".join(parts).strip()


async def discover_skills(mcp: Client) -> dict[str, dict]:
    """Map skill name -> {name, description} from skill:// resource metadata."""
    skills: dict[str, dict] = {}
    for resource in await mcp.list_resources():
        if not str(resource.uri).startswith("skill://"):
            continue
        skills[resource.name] = {
            "name": resource.name,
            "description": resource.description or "",
        }
    return skills


def build_base_prompt(skills: dict[str, dict], base: str) -> str:
    lines = [
        base.rstrip(),
        "",
        "Available skills (call load_skill with the skill name to unlock tools):",
    ]
    for name in sorted(skills):
        skill = skills[name]
        lines.append(f"- {skill['name']}: {skill['description']}")
    return "\n".join(lines)


class Agent:
    llm_name = LLM_NAME

    def __init__(
        self,
        name: str,
        prompt: str,
        tools: list,
        socket_pool: ConnPool,
        mcp: Client | None = None,
        description: str = "",
        skills: dict[str, dict] | None = None,
        tool_index: dict[str, dict] | None = None,
    ):
        self.name = name
        self.description = description
        self.prompt = prompt
        self.tools = tools  # starting tools: load_skill only
        self.socket_pool = socket_pool
        self.mcp = mcp
        self.skills = skills or {}
        self.tool_index = tool_index or {}

    async def load_skill(self, skill_name: str, active_tools: list) -> str:
        """Local tool: return skill prompt text and append its tools to active_tools."""
        if self.mcp is None:
            raise RuntimeError(f"Agent {self.name!r} has no MCP client")

        if skill_name not in self.skills:
            available = ", ".join(sorted(self.skills)) or "(none)"
            return f"Unknown skill {skill_name!r}. Available: {available}"

        info = json.loads(
            (await self.mcp.read_resource(f"skill://{skill_name}"))[0].text
        )
        skill_info = _prompt_text(await self.mcp.get_prompt(info["prompt"]))

        present = {t.get("name") for t in active_tools}
        for tool_name in info["tools"]:
            if tool_name in present:
                continue
            schema = self.tool_index.get(tool_name)
            if schema is None:
                continue
            active_tools.append(dict(schema))
            present.add(tool_name)

        return skill_info or f"Loaded skill {skill_name!r}."

    async def _call_tool(
        self,
        name: str,
        args: dict,
        active_tools: list,
    ) -> str:
        """Dispatch load_skill locally; everything else via MCP."""
        if name == "load_skill":
            skill_name = (args or {}).get("skill_name")
            if not skill_name:
                return "load_skill requires skill_name"
            return await self.load_skill(str(skill_name), active_tools)

        if self.mcp is None:
            return f"No MCP client; cannot call tool {name!r}"

        try:
            result = await self.mcp.call_tool(name, args or {})
            return _tool_output_str(result)
        except Exception as exc:
            return f"Tool {name!r} failed: {exc}"

    async def run(
        self,
        input: str,
        prev_conv: list[dict] = [],
    ) -> AsyncIterator[dict]:
        """Yield stream events; tools run between model turns.

        Events:
          {"type": "text", "text": "..."}     — provisional model text
          {"type": "tool_call", "call_id", "name", "args"}
          {"type": "delta", "text": "..."}    — final-answer text
        """

        depth = 0
        # Per-run tool list: starts with load_skill, grows as skills load
        active_tools: list = [dict(t) for t in self.tools]

        # instructions = base prompt + skills only; history stays in input
        payload: dict = {
            "type": "response.create",
            "model": self.llm_name,
            "instructions": self.prompt,
            "input": prev_conv + [{"role": "user", "content": input}],
            "tools": active_tools,
            "parallel_tool_calls": True,
            "reasoning": {"effort": "none"},
        }

        async with self.socket_pool.session() as conn:
            while True:
                depth += 1
                turn: LlmTurnResult | None = None
                text_parts: list[str] = []
                async for item in stream_turn(conn, payload):
                    if isinstance(item, LlmTurnResult):
                        turn = item
                    else:
                        text_parts.append(item)
                        # Text is provisional until the turn completes. The
                        # client renders it as WIP and promotes the final
                        # turn when the delta arrives below.
                        yield {"type": "text", "text": item}

                if turn is None:
                    raise RuntimeError("LLM turn ended without response.completed")

                if not turn.tool_calls:
                    # Preserve the existing final-answer event path. Sending
                    # the assembled text once also gives the UI a clean
                    # promotion point for the provisional WIP text.
                    final_text = "".join(text_parts)
                    if final_text:
                        yield {"type": "delta", "text": final_text}
                    break

                for call in turn.tool_calls:
                    yield {
                        "type": "tool_call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "args": call["args"],
                    }

                if depth >= MAX_DEPTH:
                    raise RuntimeError(
                        f"Agent {self.name!r} hit MAX_DEPTH ({MAX_DEPTH}) "
                        "response turns with pending tool calls"
                    )
                # do i need this?
                if not turn.response_id:
                    raise RuntimeError(
                        f"Agent {self.name!r} got tool calls without response id"
                    )

                next_depth = depth + 1
                tool_choice = "none" if next_depth >= MAX_DEPTH else "auto"
                # load_skill mutates active_tools in place; next payload gets the expanded set
                outputs = await asyncio.gather(*[
                    self._call_tool(c["name"], c["args"], active_tools)
                    for c in turn.tool_calls
                ])
                pending_outputs = [
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": output,
                    }
                    for call, output in zip(turn.tool_calls, outputs)
                ]

                payload = {
                    "type": "response.create",
                    "model": self.llm_name,
                    "previous_response_id": turn.response_id,
                    "input": pending_outputs,
                    "tools": active_tools,
                    "parallel_tool_calls": True,
                    "tool_choice": tool_choice,
                    "reasoning": {"effort": "none"},
                }

    def __str__(self):
        return (
            f"Agent(name={self.name}, description={self.description}, "
            f"skills={sorted(self.skills)}, tools={[t.get('name') for t in self.tools]})"
        )

    def __repr__(self):
        return self.__str__()

    async def __call__(self, input: str, prev_conv: list[dict] = []) -> str:
        parts: list[str] = []
        async for event in self.run(input, prev_conv):
            if event.get("type") == "delta":
                parts.append(event.get("text") or "")
        return "".join(parts)


async def load_agent(mcp: Client) -> Agent:
    """Single skills agent: catalog in prompt, local load_skill unlocks MCP tools."""
    socket_pool = await ConnPool.create(POOL_SIZE)
    try:
        tool_index = {t.name: openai_tool(t) for t in await mcp.list_tools()}
        skills = await discover_skills(mcp)
        base = _prompt_text(await mcp.get_prompt("base_prompt"))

        return Agent(
            name="agent",
            prompt=build_base_prompt(skills, base),
            tools=[LOAD_SKILL_TOOL],
            socket_pool=socket_pool,
            mcp=mcp,
            description="Single skills agent — load_skill unlocks MCP tools",
            skills=skills,
            tool_index=tool_index,
        )
    except BaseException:
        await socket_pool.close()
        raise


if __name__ == "__main__":
    async def _main():
            async with Client(MCP_SERVER_URL) as mcp:
                test = await load_agent(mcp)
                async for event in test.run(
                    "hey, what's the weather in banglore & delhi test agent, before you call any tool also provide your reason for calling the tool",
                ):
                    etype = event.get("type")
                    if etype == "text":
                        print(f"[text] {event.get('text') or ''}", flush=True)
                    elif etype == "tool_call":
                        print(
                            f"[tool_call] {event.get('name')}({event.get('args')})",
                            flush=True,
                        )
                    elif etype == "delta":
                        print(event.get("text") or "", end="", flush=True)
                print()

    asyncio.run(_main())
