# Weather MCP
from fastmcp import FastMCP

weather = FastMCP("weather")



@weather.prompt
def weather_prompt() -> str:
    return """you now have access to the following tools:
    - get_coordinates
    - get_weather
    - get_wind_speed
    - get_wind_direction
    - get_precipitation
    you can use these tools to get the weather, wind speed, wind direction, or precipitation for a given location.
    """

@weather.tool
async def get_coordinates(location: str) -> tuple[float, float]:
        """
        Get the coordinates for a given location.
        Args:
            location: City or place name
        Returns:
            (latitude, longitude)
        """
        return (12.9716, 77.5946)

@weather.tool
async def get_weather(lat: float, lon: float) -> str:
    """
    Get the weather for given coordinates.
    Args:
        lat: Latitude
        lon: Longitude
    Returns:
        Short weather description
    """
    return f"The weather at ({lat}, {lon}) is sunny, 28°C"

@weather.tool
async def get_wind_speed(lat: float, lon: float) -> str:
    """Get wind speed for coordinates."""
    return "10 km/h"

@weather.tool
async def get_wind_direction(lat: float, lon: float) -> str:
    """Get wind direction for coordinates."""
    return "N"

@weather.tool
async def get_precipitation(lat: float, lon: float) -> str:
    """Get precipitation for coordinates."""
    return "0 mm"


@weather.resource("skill://weather", name="weather", description="Get the weather, wind speed, wind direction, or precipitation for a given location.")
async def weather_skill() -> dict:
    return {
        "prompt": "weather_prompt",
        "tools": ["get_coordinates", "get_weather", "get_wind_speed", "get_wind_direction", "get_precipitation"],
    }