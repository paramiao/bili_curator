#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量将同目录下同名的 .mp4（取视频轨）与 .m4a（取音频轨）合并，生成同名 .mp4。
- 保留原 mp4 文件名与路径（Emby 依赖同名 jpg/nfo，请勿改名）。
- 默认仅在 mp4 无音轨时才合并（方案A），可切换为强制覆盖（方案B）。
- 自动记录日志；失败时尝试将音频转码为 AAC 以最大化兼容性。

使用示例：
  仅处理无音轨的 mp4（方案A，默认）：
    ./merge_mp4_m4a.py --root "/Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili"

  强制用同名 m4a 替换 mp4 的音轨（方案B，建议先小范围试跑）：
    ./merge_mp4_m4a.py --root "/Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili" --mode B

  仅查看将要处理的配对（不修改文件）：
    ./merge_mp4_m4a.py --root ... --dry-run
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

FNUM_RE = re.compile(r"\.f(\d{3,6})(\.[A-Za-z0-9]{2,4})$")


def log_open(path):
    return open(path, 'a', encoding='utf-8')


def have(cmd: str) -> bool:
    # Simple `which` equivalent for checking command existence.
    for path in os.environ["PATH"].split(os.pathsep):
        if os.access(os.path.join(path, cmd), os.X_OK):
            return True
    return False


def norm_base(name: str) -> str:
    # Normalizes 'video.f30280.mp4' to 'video.mp4'
    return re.sub(r"\.f\d{3,6}(\.[A-Za-z0-9]{2,4})$", r"\1", name)


def extract_fnum(name: str) -> int:
    m = FNUM_RE.search(name)
    return int(m.group(1)) if m else -1


def has_audio(mp4_path: str) -> bool:
    try:
        p = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', mp4_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        return any(line.strip() for line in p.stdout.splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False # ffprobe error or not found, assume no audio to be safe


def pick_m4a(m4a_list: List[str]) -> Optional[str]:
    # Sort by f-number descending, then by name descending.
    m4a_list = sorted(m4a_list, key=lambda p: (extract_fnum(os.path.basename(p)), os.path.basename(p)), reverse=True)
    return m4a_list[0] if m4a_list else None


def pick_mp4_target(mp4_list: List[str], mode: str, logf) -> Optional[str]:
    cands = mp4_list
    if mode == 'A':
        cands = [p for p in mp4_list if not has_audio(p)]
        if not cands:
            return None

    def sort_key(p):
        name = os.path.basename(p)
        no_f = 1 if not FNUM_RE.search(name) else 0
        fnum = extract_fnum(name)
        return (no_f, fnum, name)

    cands.sort(key=sort_key, reverse=True)
    return cands[0]


def mux(mp4: str, m4a: str, logf, transcode_fallback: bool = True) -> bool:
    base, _ = os.path.splitext(mp4)
    tmp = base + '.__mux.tmp.mp4'
    bak = mp4 + '.bak'

    def ff(cmd: List[str]) -> int:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE).returncode

    logf.write(f"MUX(copy): video='{mp4}' audio='{m4a}' -> '{tmp}'\n"); logf.flush()
    rc = ff(['ffmpeg', '-y', '-v', 'error', '-i', mp4, '-i', m4a,
             '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'copy', '-movflags', '+faststart', tmp])

    if rc != 0 and transcode_fallback:
        logf.write(f"  -> Retry with AAC transcode...\n"); logf.flush()
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        rc = ff(['ffmpeg', '-y', '-v', 'error', '-i', mp4, '-i', m4a,
                 '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', tmp])

    if rc != 0 or not os.path.exists(tmp):
        logf.write(f"  -> FFMPEG FAILED for '{mp4}'\n"); logf.flush()
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        return False

    try:
        if os.path.exists(bak):
            os.remove(bak)
        os.rename(mp4, bak)
        os.rename(tmp, mp4)
        return True
    except Exception as e:
        logf.write(f"  -> ERROR replacing '{mp4}': {e}\n"); logf.flush()
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        # Try to restore backup
        if not os.path.exists(mp4) and os.path.exists(bak):
            os.rename(bak, mp4)
        return False

def main():
    parser = argparse.ArgumentParser(description='将同目录下同名的 .mp4 与 .m4a 合并。')
    parser.add_argument('--root', required=True, help='Bilibili 下载根目录')
    parser.add_argument('--mode', default='A', choices=['A', 'B'], help='A: 仅合并无音轨的mp4 (默认); B: 强制覆盖mp4音轨')
    parser.add_argument('--log', help='日志文件路径 (默认: --root/merge_audio.log)')
    parser.add_argument('--dry-run', action='store_true', help='仅打印将要处理的文件配对，不实际操作')
    parser.add_argument('--no-transcode-fallback', action='store_true', help='禁用音频流复制失败时的 AAC 转码回退')
    args = parser.parse_args()

    if not have('ffmpeg') or not have('ffprobe'):
        print("错误：找不到 ffmpeg 或 ffprobe，请确保已安装并加入 PATH。", file=sys.stderr)
        sys.exit(1)

    log_path = args.log or os.path.join(args.root, 'merge_audio.log')
    logf = log_open(log_path)
    logf.write(f"\n{'='*80}\n")
    logf.write(f"开始处理: {datetime.datetime.now().isoformat()}\n")
    logf.write(f"参数: root='{args.root}', mode='{args.mode}', dry_run={args.dry_run}\n")
    logf.flush()

    file_groups: Dict[str, List[str]] = {}
    for dirpath, _, filenames in os.walk(args.root):
        for name in filenames:
            if not any(name.lower().endswith(ext) for ext in ['.mp4', '.m4a']):
                continue
            # Group by directory and normalized name
            base = norm_base(os.path.splitext(name)[0])
            key = os.path.join(dirpath, base)
            if key not in file_groups:
                file_groups[key] = []
            file_groups[key].append(os.path.join(dirpath, name))

    total_pairs = 0
    processed_count = 0
    for key, paths in file_groups.items():
        mp4s = [p for p in paths if p.lower().endswith('.mp4')]
        m4as = [p for p in paths if p.lower().endswith('.m4a')]

        if not mp4s or not m4as:
            continue

        target_mp4 = pick_mp4_target(mp4s, args.mode, logf)
        if not target_mp4:
            logf.write(f"SKIP(no_target): '{mp4s[0]}' (mode={args.mode})\n")
            continue

        target_m4a = pick_m4a(m4as)
        if not target_m4a: continue

        total_pairs += 1
        logf.write(f"MATCH: MP4='{target_mp4}', M4A='{target_m4a}'\n")

        if args.dry_run:
            continue

        if mux(target_mp4, target_m4a, logf, not args.no_transcode_fallback):
            processed_count += 1
            logf.write(f"  -> SUCCESS: '{target_mp4}'\n")
        else:
            logf.write(f"  -> FAILED: '{target_mp4}'\n")
        logf.flush()

    summary = (
        f"处理完成: {datetime.datetime.now().isoformat()}\n"
        f"找到 {total_pairs} 个潜在配对，"
        f"{'实际处理' if not args.dry_run else '若执行将处理'} {processed_count} 个。\n"
        f"{'='*80}\n"
    )
    logf.write(summary)
    logf.close()
    print(summary.strip())

if __name__ == '__main__':
    main()