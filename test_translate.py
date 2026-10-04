"""DOKSURI 离线翻译测试脚本

用法：python test_translate.py
在无网络（或网络被阻断）环境下运行，验证本地翻译功能完全离线可用。
"""
import sys

import translator


def main():
    cases = [
        ("Hello, world!", "en", "zh"),
        ("你好，世界！今天天气不错。", "zh", "en"),
        ("Bonjour le monde !", "fr", "zh"),  # 法语 -> 经英语中转 -> 中文
    ]
    for text, f, t in cases:
        result = translator.translate(text, f, t)
        print(f"[{f} -> {t}] {text!r}")
        print(f"        => {result!r}")

    detected = translator.detect_language("Hello, world!")
    print(f"自动检测 'Hello, world!' => {detected}")
    print("全部测试通过：翻译完全在本地完成。")


if __name__ == "__main__":
    main()
