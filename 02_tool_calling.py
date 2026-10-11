"""
02_tool_calling.py
==================
Expanding to Tool Calling / Function Calling in LiveKit Agents.

Key Concepts:
- `@function_tool`: Decorator exposing a Python method to the LLM as a callable tool.
- Type annotations (`str`, `int`, `float`, `bool`) & docstrings are parsed into JSON Schema.
- The LLM automatically decides when to invoke the tool based on conversation context.
- While the tool runs, the agent can inform the user or keep audio latency low.
"""

import logging
from typing import Annotated
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    function_tool,
    inference,
)
from livekit.plugins import silero

load_dotenv()
logger = logging.getLogger("tool-calling-agent")
logger.setLevel(logging.INFO)


class AssistantWithTools(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
                You are a smart personal voice assistant.
                You have tools to fetch current weather and look up appointments.
                - Keep answers spoken and natural.
                - When the user asks about weather or schedule, call your tools before answering.
            """
        )

    async def on_enter(self) -> None:
        await self.session.say("Hello! I can check weather and calendar events for you. What would you like to know?")

    @function_tool
    async def get_current_weather(
        self,
        location: Annotated[str, "The city and state or country, e.g. San Francisco, CA or London, UK"],
    ) -> str:
        """Fetch the current weather for a specified location."""
        logger.info(f"Tool called: get_current_weather for {location}")
        # In a real app, query OpenWeatherMap or WeatherAPI
        return f"The weather in {location} is currently 72°F (22°C) and sunny with a gentle breeze."

    @function_tool
    async def check_calendar(
        self,
        date_description: Annotated[str, "The date to inspect, e.g. 'today' or 'tomorrow'"],
    ) -> str:
        """Look up user calendar meetings and scheduled events."""
        logger.info(f"Tool called: check_calendar for {date_description}")
        return f"On {date_description}, you have one meeting: 'Team AI Architecture Sync' at 3:00 PM."


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3-general"),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        ),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    agent = AssistantWithTools()
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
