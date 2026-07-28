"""Pipecat minimal local voice agent demo.

Stack (no Daily/LiveKit account needed):
- Transport : LocalAudioTransport (mic + speaker, PyAudio)
- VAD       : Silero (bundled onnx)
- STT       : faster-whisper (local, model from hf-mirror)
- LLM       : OpenAILLMService -> DashScope (Alibaba Bailian) OpenAI-compatible endpoint
- TTS       : Piper (local onnx voice, pre-downloaded via hf-mirror)

Keys: reads DASHSCOPE_API_KEY from the sibling voice-agent project's .env.
"""

import asyncio
import os
import sys
from pathlib import Path

# hf-mirror for model downloads (huggingface.co blocked in this network)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from dotenv import load_dotenv
from loguru import logger

# Load DashScope key from sibling project (do not print it)
load_dotenv(r"D:\Documents\Projects\Chaonao\project\voice-agent\.env")
load_dotenv(override=True)

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = os.environ.get("PIPECAT_DEMO_LLM", "qwen-flash")
PIPER_VOICES_DIR = Path(__file__).parent / "piper-voices"


async def main():
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        )
    )

    # Use locally cached faster-whisper-large-v3-turbo snapshot directly
    # (offline HF cache has no refs/, so pass the snapshot dir path)
    WHISPER_MODEL_DIR = (
        Path.home()
        / ".cache/huggingface/hub/models--Systran--faster-whisper-large-v3-turbo/snapshots/main"
    )
    stt = WhisperSTTService(
        model=str(WHISPER_MODEL_DIR),
        device="cpu",
        compute_type="int8",
    )

    llm = OpenAILLMService(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        base_url=DASHSCOPE_BASE_URL,
        settings=OpenAILLMService.Settings(
            model=LLM_MODEL,
            system_instruction=(
                "You are a helpful assistant in a voice conversation. "
                "Your responses will be spoken aloud, so avoid emojis, bullet points, "
                "or other formatting that can't be spoken. Respond briefly."
            ),
            # Qwen3 thinking control: pipecat spreads `extra` as kwargs to
            # chat.completions.create(), so wrap in extra_body for the OpenAI SDK
            extra={"extra_body": {"enable_thinking": False}},
        ),
    )
    # DashScope's OpenAI-compatible endpoint rejects the "developer" role;
    # pipecat will convert developer messages to "user" when this is False.
    llm.supports_developer_role = False

    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice="en_US-lessac-medium"),
        download_dir=PIPER_VOICES_DIR,
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    context.add_message({"role": "developer", "content": "Please introduce yourself to the user."})
    await task.queue_frames([LLMRunFrame()])

    runner = PipelineRunner(handle_sigint=False if sys.platform == "win32" else True)

    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
