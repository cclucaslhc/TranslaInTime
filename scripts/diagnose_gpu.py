import os
import sys

import ctranslate2


def main() -> int:
    print(f"Python: {sys.version}")
    print(f"CTranslate2: {ctranslate2.__version__}")
    print(f"CUDA device count: {ctranslate2.get_cuda_device_count()}")
    print(f"WHISPER_DEVICE={os.getenv('WHISPER_DEVICE', 'auto')}")
    print(f"WHISPER_COMPUTE_TYPE={os.getenv('WHISPER_COMPUTE_TYPE', 'int8_float16')}")
    if ctranslate2.get_cuda_device_count() < 1:
        print("CTranslate2 cannot see a CUDA device. The app will fall back to CPU.")
        return 1
    print("CTranslate2 can see CUDA. Faster-Whisper should be able to use the RTX GPU.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
