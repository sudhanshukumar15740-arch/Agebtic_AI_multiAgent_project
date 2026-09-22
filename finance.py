# Finance MCP
from fastmcp import FastMCP

finance = FastMCP("finance")



@finance.prompt
def finance_prompt() -> str:
    return """you now have access to the following tools:
    - get_stock_price
    - get_market_status
    - get_company_name
    you can use these tools to get the latest stock price, market status, or company name.
    """

@finance.tool
async def get_stock_price(symbol: str) -> float:
    """
    Get the latest mock stock price for a ticker symbol.
    Args:
        symbol: Stock ticker, e.g. AAPL, MSFT, GOOG
    Returns:
        Price in USD
    """
    return f"The latest stock price for {symbol.upper()} is $100.00"

@finance.tool
async def get_market_status(exchange: str) -> str:
    """
    Get whether a market/exchange is open (mock).
    Args:
        exchange: Exchange code, e.g. NASDAQ, NYSE, NSE
    Returns:
        Open/closed status string
    """
    if exchange[0] > 5:
        return f"The {exchange.upper()} market is open"
    return f"The {exchange.upper()} market is closed"

@finance.tool
async def get_company_name(symbol: str) -> str:
    """
    Resolve a ticker symbol to a company name (mock).
    Args:
        symbol: Stock ticker
    Returns:
        Company display name
    """
    return f"The company name for {symbol.upper()} is Apple Inc."


@finance.resource("skill://finance", name="finance", description="Get the latest stock price, market status, or company name.")
async def finance_skill() -> dict:
    return {
        "prompt": "finance_prompt",
        "tools": ["get_stock_price", "get_market_status", "get_company_name"],
    }