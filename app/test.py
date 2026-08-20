import subprocess
import imageio_ffmpeg

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
url = "http://8.156.85.152:8000/uploads/ee6352f264ae4378aa56aa6cb272e135.mp4"

# 调用 ffmpeg 查看媒体信息（信息输出在 stderr）
result = subprocess.run(
    [ffmpeg_path, "-i", url],
    capture_output=True,
    text=True,
    timeout=30
)
output = result.stderr  # ffmpeg 将文件信息输出到 stderr

print(output)
# 检查输出中是否包含 "Audio:" 字符串
if "Audio:" in output:
    print("视频包含音频轨道")
else:
    print("视频没有音频轨道")