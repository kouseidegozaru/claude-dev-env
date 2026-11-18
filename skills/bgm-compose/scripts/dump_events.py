# 互換シム: 実体は bgm-sound スキルの scripts/dump_events.py（作曲／音色のスキル分割で移動）。旧パスからの呼び出し用。
import os
import runpy
import sys

sys.argv[0] = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "bgm-sound", "scripts", "dump_events.py"))
runpy.run_path(sys.argv[0], run_name="__main__")
