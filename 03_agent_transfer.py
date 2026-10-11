"""
03_agent_transfer.py
====================
Multi-Agent Orchestration & Seamless Mid-Call Handoff.

Key Concepts:
- Real-world voice AI often needs specialized persona/department handling:
  e.g., Receptionist / Triage -> Technical Support -> Billing Specialist.
- `self.session.update_agent(NewAgent())`: Instantly switches active instructions,
  tools, and persona within the same ongoing WebRTC audio stream.
- Zero audio dropout during transfer; the user stays on the line smoothly.
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
    function_tool,
    inference,
)
from livekit.plugins import silero

load_dotenv()
logger = logging.getLogger("multi-agent-transfer")
logger.setLevel(logging.INFO)


class ReceptionistAgent(Agent):
    """Front-desk triage agent that routes calls to specialized departments."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
                You are the Front Desk receptionist for TechCorp.
                Your job is to greet the caller, understand what they need, and transfer them:
                - For billing or invoice questions, call `transfer_to_billing`.
                - For technical problems or bugs, call `transfer_to_tech_support`.
                Keep answers concise and friendly.
            """
        )

    async def on_enter(self) -> None:
        await self.session.say("Welcome to TechCorp! Are you calling for technical support or billing?")

    @function_tool
    async def transfer_to_billing(self) -> str:
        """Transfer caller to the billing department."""
        logger.info("Routing caller to BillingAgent")
        self.session.update_agent(BillingAgent())
        return "Transferring to billing now."

    @function_tool
    async def transfer_to_tech_support(self) -> str:
        """Transfer caller to technical support."""
        logger.info("Routing caller to TechSupportAgent")
        self.session.update_agent(TechSupportAgent())
        return "Transferring to technical support now."


class BillingAgent(Agent):
    """Specialized Billing Department Agent."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
                You are Alex from TechCorp Billing.
                You help callers with invoices, payments, and subscription plans.
                If they ask to speak to tech support or front desk, you can transfer them back using `transfer_to_front_desk`.
            """
        )

    async def on_enter(self) -> None:
        await self.session.say("Hello, this is Alex in billing. How can I assist with your account or invoice?")

    @function_tool
    async def transfer_to_front_desk(self) -> str:
        """Transfer back to the main receptionist."""
        logger.info("Returning caller to ReceptionistAgent")
        self.session.update_agent(ReceptionistAgent())
        return "Transferring back to reception."


class TechSupportAgent(Agent):
    """Specialized Technical Support Agent."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
                You are Jordan from TechCorp Tier-2 Technical Support.
                You help callers troubleshoot system errors, connection issues, and software bugs.
                If they need billing instead, use `transfer_to_billing`.
            """
        )

    async def on_enter(self) -> None:
        await self.session.say("Hi, you've reached Jordan in tech support. What issue are you experiencing?")

    @function_tool
    async def transfer_to_billing(self) -> str:
        """Transfer to billing."""
        logger.info("Routing caller to BillingAgent")
        self.session.update_agent(BillingAgent())
        return "Transferring to billing."


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

    # Begin the call with the ReceptionistAgent
    initial_agent = ReceptionistAgent()
    await session.start(agent=initial_agent, room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
