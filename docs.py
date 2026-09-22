# Documentation MCP
from fastmcp import FastMCP

docs = FastMCP("docs")



@docs.prompt
def docs_prompt() -> str:
    return """you now have access to the following tools:
    - search_docs
    - get_doc_by_id
    - list_doc_ids
    you can use these tools to search for documentation or get a document by id or list all document ids.
    """

@docs.tool
async def search_docs(query: str) -> str:
    """
    Search internal docs and return a short mock snippet.
    Args:
        query: Search query
    Returns:
        Relevant documentation excerpt
    """
    return f"Docs: No exact hit for '{query}'. Try weather, stock, or email topics."

@docs.tool
async def get_doc_by_id(doc_id: str) -> str:
    """
    Fetch a document by id (mock).
    Args:
        doc_id: Document identifier, e.g. doc_weather, doc_finance
    Returns:
        Full mock document body
    """
    return f"Document '{doc_id}' not found"

@docs.tool
async def list_doc_ids(topic: str) -> list[str]:
    """
    List document ids for a topic (mock).
    Args:
        topic: Topic keyword, e.g. weather, finance, travel, email
    Returns:
        Matching document ids
    """
    return ["doc_weather", "doc_finance", "doc_travel", "doc_email"]


@docs.resource("skill://docs", name="docs", description="Search internal docs and get a document by id or list all document ids.")
async def docs_skill() -> dict:
    return {
        "prompt": "docs_prompt",
        "tools": ["search_docs", "get_doc_by_id", "list_doc_ids"],
    }