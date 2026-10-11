"""
01_listen_and_respond.py
========================
The foundational voice AI agent using LiveKit Agents 1.0+.

Core Concepts:
1. AgentServer: Handles worker registration, health checks, and dispatches jobs from LiveKit server.
2. Prewarming: Loads heavy neural models (like Silero VAD) once at process boot into process memory.
3. JobContext & RTC Session: The live session created when a participant joins a room or places a call.
4. AgentSession: Orchestrates real-time audio pipeline:
   - VAD (Voice Activity Detection): Detects speech start/end and user interruptions.
   - STT (Speech to Text): Streams user microphone audio into text transcripts.
   - LLM (Large Language Model): Generates streaming text replies.
   - TTS (Text to Speech): Converts LLM stream into audio frames streamed back to WebRTC room.
   - Preemptive generation: Starts TTS generation eagerly while LLM is still generating tokens.
"""

import logging
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
)
from livekit.plugins import silero

# Load API keys from .env file
load_dotenv()

# Configure logging
logger = logging.getLogger("basic-voice-agent")
logger.setLevel(logging.INFO)


class ListenAndRespondAgent(Agent):
    """
    The Agent encapsulates prompt instructions, personality, state, and tools.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions="""
                You are a friendly, helpful voice AI assistant.
                You are communicating directly through real-time voice.
                - Keep your responses natural, conversational, and relatively concise (1-2 sentences where appropriate).
                - Do not use markdown, emojis, bullet points, or unpronounceable characters because they will be read aloud by TTS.
            """
        )

    async def on_enter(self) -> None:
        """
        Called as soon as the agent enters the room and starts the session.
        We trigger an initial greeting.
        """
        await self.session.say("Hello! I am your AI assistant. How can I help you today?")


# 1. Initialize the AgentServer worker
server = AgentServer()


# 2. Worker prewarm hook: cache heavy models in proc.userdata before accepting jobs
def prewarm(proc: JobProcess):
    logger.info("Prewarming worker: loading Silero VAD model...")
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


# 3. Main RTC entrypoint for every connected room / call
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    ctx.log_context_fields = {"room": ctx.room.name}

    # Set up the streaming pipeline session
    # Uses LiveKit inference plugin wrappers or specific provider plugins (Deepgram, OpenAI, Cartesia)
    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3-general"),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",  # Friendly Cartesia voice ID
        ),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,  # Lowers latency by pipelining LLM tokens directly into TTS
    )

    agent = ListenAndRespondAgent()

    # Start the agent session attached to the LiveKit room
    await session.start(agent=agent, room=ctx.room)

    # Connect worker to the room audio/video tracks
    await ctx.connect()
    logger.info("Agent is live and listening in room.")


if __name__ == "__main__":
    # cli.run_app starts the worker CLI.
    # To run locally with terminal audio:
    #   python 01_listen_and_respond.py console
    # To run connected to LiveKit Cloud:
    #   python 01_listen_and_respond.py dev
    cli.run_app(server)
