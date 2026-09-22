# Email MCP
from fastmcp import FastMCP

email = FastMCP("email")



@email.prompt
def email_prompt() -> str:
    return """you now have access to the following tools:
    - send_email
    - list_drafts
    - get_draft
    you can use these tools to send an email, list drafts, or get a draft by id.
    """

@email.tool
async def send_email(to: str, subject: str, body: str) -> str:
        """
        Queue a mock email for delivery.
        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body text
        Returns:
            Confirmation string with a fake message id
        """
        return f"queued to {to} | subject={subject!r} | id=msg_mock_001 | chars={len(body)}"

@email.tool
async def list_drafts(mailbox: str) -> list[str]:
    """
    List draft subjects for a mailbox (mock).
    Args:
        mailbox: Mailbox name or email address
    Returns:
        Draft subject lines
    """
    return ["Weekly status update", "Trip itinerary draft"]

@email.tool
async def get_draft(draft_id: str) -> str:
    """
    Fetch a draft email body by id (mock).
    Args:
        draft_id: Draft identifier, e.g. draft_1
    Returns:
        Draft body text
    """
    return f"Draft '{draft_id}' not found"


@email.resource("skill://email", name="email", description="Send an email, list drafts, or get a draft by id.")
async def email_skill() -> dict:
    return {
        "prompt": "email_prompt",
        "tools": ["send_email", "list_drafts", "get_draft"],
    }