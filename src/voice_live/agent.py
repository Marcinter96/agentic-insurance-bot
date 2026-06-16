"""Builds the ADK live agent, runner, request queue, and run configuration."""

from __future__ import annotations

from dataclasses import dataclass

from google.adk.agents import LlmAgent
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import InMemoryRunner
from google.genai import types

from voice_live.config import Settings
from voice_live.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class LiveAgentBundle:
    """Everything needed to drive a single live session."""

    runner: InMemoryRunner
    live_request_queue: LiveRequestQueue
    run_config: RunConfig
    settings: Settings


def build_agent(settings: Settings) -> LlmAgent:
    """Return the insurance_bot live root agent on our Live model.

    Imported lazily so load_settings() (AI Studio backend) runs first.
    """
    from insurance_bot.live_agent import root_agent

    root_agent.live_model = settings.model
    return root_agent


async def build_session(settings: Settings) -> LiveAgentBundle:
    """Build the runner + ADK session + live queue + BIDI/AUDIO run config."""
    agent = build_agent(settings)
    logger.info("[bold cyan]MODEL[/bold cyan]     %s  [dim](ADK run_live, AI Studio)[/dim]", settings.model)

    runner = InMemoryRunner(app_name=settings.app_name, agent=agent)
    await runner.session_service.create_session(
        app_name=settings.app_name,
        user_id=settings.user_id,
        session_id=settings.session_id,
        state={"session_id": settings.session_id},
    )

    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    logger.info("RunConfig: BIDI, AUDIO, transcription=ON")

    return LiveAgentBundle(
        runner=runner,
        live_request_queue=LiveRequestQueue(),
        run_config=run_config,
        settings=settings,
    )
