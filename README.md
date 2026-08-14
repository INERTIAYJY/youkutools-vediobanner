# 批量视频处理客户端

Windows 本地桌面客户端，支持 `A+B 视频拼接`、竖版贴片和横版贴片三种批量导出模式。

## 功能

- A+B 拼接：导入固定视频 A 和批量视频 B 文件夹，按 `A + 每个B` 的规则逐个导出成品
- 竖版贴片：导入视频文件夹、贴片 A/B（图片或视频）或贴片素材文件夹，批量导出 `上贴片 + 中间 16:9 视频 + 下贴片`
- 横版贴片：导入竖版视频文件夹、贴片 A/B（图片或视频）或贴片素材文件夹，批量导出 `左贴片 + 中间 9:16 视频 + 右贴片`
- 贴片素材支持单图/单视频和批量素材自动识别，图片与视频可混用；批量素材每次扫描都会刷新随机组合，并按上贴片、下贴片和完整组合的使用次数分散分配
- 视频贴片比主视频短时自动循环播放补满全程；贴片输出不带声音
- 竖版贴片固定中间 16:9 视频区域；横版贴片固定中间 9:16 视频区域；视频完整保留不裁切，贴片素材自动裁切填满贴片区域
- 选择或拖入素材路径和导出目录
- A+B 拼接提供全部规格：`1080x1920`、`720x1280`、`1920x1080`、`1080x1080`、`1280x720`
- 竖版贴片提供 `1080x1920`、`1080x1080`、`720x1280`；横版贴片提供 `1920x1080`、`1280x720`
- 选择视频码率：高码率 `20 Mbps`、中码率 `15 Mbps`、低码率 `8 Mbps`
- 自动扫描 `.mp4`、`.mov`、`.mkv`、`.avi`
- 自动扫描贴片素材：图片 `.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp` 与视频 `.mp4`、`.mov`、`.mkv`、`.avi`
- 稳定优先转码为 `MP4 / H.264 / AAC`
- 保持比例缩放，不裁切，居中补黑边
- A+B 拼接会顺序保留 A 段和 B 段音频；贴片模式输出无声；无音频素材自动补静音
- 单任务顺序批量处理，失败跳过并记录日志；取消后自动清理半成品
- 输出目录不能与待处理视频目录相同，避免成品再次进入扫描
- 不覆盖已有成品；重复导出自动生成新文件名
- 支持取消和安全关闭；暂停会在当前文件处理完成后生效

## 开发运行

```powershell
$env:PYTHONPATH="src"
python src\batch_ab_video\main.py
```

## FFmpeg

程序优先查找：

```text
tools/ffmpeg/bin/ffmpeg.exe
tools/ffmpeg/bin/ffprobe.exe
```

如果不存在，则使用系统 PATH 里的 `ffmpeg` 和 `ffprobe`。

## 打包

将 `ffmpeg.exe` 和 `ffprobe.exe` 放入 `tools/ffmpeg/bin/` 后执行：

```powershell
pyinstaller build\batch_ab_video.spec --clean --noconfirm
```

输出目录：

```text
dist/BatchABVideo/
```

每次导出会生成带时间戳的 `export_log_YYYYMMDD_HHMMSS.csv`，并同步更新最新的 `export_log.csv`。

## 测试

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```
