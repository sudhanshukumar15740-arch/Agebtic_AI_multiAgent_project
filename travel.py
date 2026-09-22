# Travel MCP
from fastmcp import FastMCP

travel = FastMCP("travel")



@travel.prompt
def travel_prompt() -> str:
    return """you now have access to the following tools:
    - get_travel_time
    - get_travel_cost
    - get_distance_km
    you can use these tools to get the travel time, cost, or distance between two places.
    """

@travel.tool
async def get_travel_time(origin: str, destination: str) -> str:
        """
        Estimate travel time between two places (mock).
        Args:
            origin: Start city
            destination: End city
        Returns:
            Human-readable duration
        """
        return f"The travel time from {origin} to {destination} is 2 hours"

@travel.tool
async def get_travel_cost(origin: str, destination: str) -> float:
    """
    Estimate travel cost between two places (mock).
    Args:
        origin: Start city
        destination: End city
    Returns:
        Cost estimate in USD
    """
    return 200.0

@travel.tool
async def get_distance_km(origin: str, destination: str) -> float:
    """
    Get approximate distance in kilometers (mock).
    Args:
        origin: Start city
        destination: End city
    Returns:
        Distance in km
    """
    return 500.0


@travel.resource("skill://travel", name="travel", description="Get the travel time, cost, or distance between two places.")
async def travel_skill() -> dict:
    return {
        "prompt": "travel_prompt",
        "tools": ["get_travel_time", "get_travel_cost", "get_distance_km"],
    }