"""FastRTC 最小 echo demo:浏览器麦克风 -> WebRTC -> 服务端回显。

运行: python echo_demo.py 然后打开 http://localhost:7860
无需任何 API key。
"""

import numpy as np
from fastrtc import ReplyOnPause, Stream


def detection(audio: tuple[int, np.ndarray]):
    # 原样回显收到的音频
    yield audio


stream = Stream(
    handler=ReplyOnPause(detection),
    modality="audio",
    mode="send-receive",
)

if __name__ == "__main__":
    stream.ui.launch(server_port=7860)
