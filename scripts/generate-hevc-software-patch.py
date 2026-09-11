#!/usr/bin/env python3
"""Generate0061 from pinned source plus the actual isolated FFmpeg build delta."""
import argparse, base64, difflib, hashlib, json
from pathlib import Path
import subprocess, urllib.request

ROOT=Path(__file__).resolve().parents[1]
PIN='79460ebecaa5625e57a5fb679a735659e73dc687'
FFMPEG='2b68d2babae73714846961fb0ee47e3b3d2e39a9'
PATHS=['media/ffmpeg/ffmpeg_common.cc','media/base/supported_types.cc','media/filters/ffmpeg_video_decoder.cc',
       'media/filters/ffmpeg_video_decoder_unittest.cc','media/ffmpeg/scripts/build_ffmpeg.py']

def replace_once(text,old,new):
    assert text.count(old)==1, old[:100]
    return text.replace(old,new)

def edits(path,text):
    if path.endswith('build_ffmpeg.py'):
        return replace_once(text,"    configure_flags['ChromeAndroid'].extend([",
            "    if target_os == 'linux' and target_arch == 'x64':\n"
            "        configure_flags['Chrome'].extend([\n"
            "            '--enable-decoder=hevc', '--enable-parser=hevc',\n"
            "        ])\n\n    configure_flags['ChromeAndroid'].extend([")
    if path.endswith('ffmpeg_common.cc'):
        return replace_once(text, '  return "h264";\n',
            '#if BUILDFLAG(ENABLE_PLATFORM_HEVC)\n'
            '  if (avcodec_find_decoder(AV_CODEC_ID_HEVC)) {\n'
            '    return "h264,hevc";\n'
            '  }\n#endif\n'
            '  return "h264";\n')
    if path.endswith('supported_types.cc'):
        text=replace_once(text, '#include "ui/gfx/hdr_metadata.h"\n',
            '#include "ui/gfx/hdr_metadata.h"\n\n'
            '#if BUILDFLAG(ENABLE_FFMPEG_VIDEO_DECODERS) && BUILDFLAG(USE_PROPRIETARY_CODECS)\n'
            'extern "C" {\n#include "third_party/ffmpeg/libavcodec/avcodec.h"\n}\n#endif\n')
        text=replace_once(text,'#if BUILDFLAG(ENABLE_PLATFORM_HEVC)\n#if BUILDFLAG(PLATFORM_HAS_OPTIONAL_HEVC_DECODE_SUPPORT)',
            '#if BUILDFLAG(ENABLE_PLATFORM_HEVC)\n'
            '  if ((type.profile == HEVCPROFILE_MAIN ||\n'
            '       type.profile == HEVCPROFILE_MAIN10) &&\n'
            '      IsDecoderBuiltInVideoCodec(VideoCodec::kHEVC)) {\n'
            '    return true;\n'
            '  }\n'
            '#if BUILDFLAG(PLATFORM_HAS_OPTIONAL_HEVC_DECODE_SUPPORT)')
        text=replace_once(text,'  if (codec == VideoCodec::kH264) {\n    return true;\n  }\n',
            '  if (codec == VideoCodec::kH264) {\n    return true;\n  }\n'
            '#if BUILDFLAG(ENABLE_PLATFORM_HEVC)\n'
            '  if (codec == VideoCodec::kHEVC) {\n'
            '    // Query the registered implementation: other FFmpeg target\n'
            '    // configurations may still omit this software decoder.\n'
            '    return avcodec_find_decoder(AV_CODEC_ID_HEVC) != nullptr;\n'
            '  }\n#endif\n')
        return text
    if path.endswith('ffmpeg_video_decoder.cc'):
        text=replace_once(text,'    case VideoCodec::kHEVC:\n','')
        text=replace_once(text,'    case VideoCodec::kH264:\n','    case VideoCodec::kH264:\n    case VideoCodec::kHEVC:\n')
        text=replace_once(text,'  // We only build support for H.264.\n  return codec == VideoCodec::kH264 && IsDecoderBuiltInVideoCodec(codec);',
            '  return (codec == VideoCodec::kH264 || codec == VideoCodec::kHEVC) &&\n'
            '         IsDecoderBuiltInVideoCodec(codec);')
        text=replace_once(text,'  if (!IsCodecSupported(config.codec()) ||\n      !ConfigureDecoder(config, low_delay)) {',
            '  const bool unsupported_hevc_profile =\n'
            '      config.codec() == VideoCodec::kHEVC &&\n'
            '      config.profile() != HEVCPROFILE_MAIN &&\n'
            '      config.profile() != HEVCPROFILE_MAIN10;\n'
            '  if (!IsCodecSupported(config.codec()) || unsupported_hevc_profile ||\n'
            '      !ConfigureDecoder(config, low_delay)) {')
        return text
    if path.endswith('ffmpeg_video_decoder_unittest.cc'):
        text=replace_once(text, '  void InitializeWithConfigWithResult(const VideoDecoderConfig& config,',
            '  bool InitializeWithConfigWithResult(const VideoDecoderConfig& config,')
        text=replace_once(text, '                                      bool success) {\n    decoder_->Initialize(',
            '                                      bool success) {\n    bool initialized = false;\n    decoder_->Initialize(')
        text=replace_once(text, '            [](bool success, DecoderStatus status) {\n              EXPECT_EQ(status.is_ok(), success);\n            },\n            success),',
            '            [](bool success, bool* initialized, DecoderStatus status) {\n'
            '              *initialized = status.is_ok();\n'
            '              EXPECT_EQ(*initialized, success);\n'
            '            },\n            success, &initialized),')
        text=replace_once(text, '    base::RunLoop().RunUntilIdle();\n  }\n\n  void InitializeWithConfig(',
            '    base::RunLoop().RunUntilIdle();\n    return initialized;\n  }\n\n  void InitializeWithConfig(')
        text=replace_once(text,'#include "media/base/test_data_util.h"', '#include "media/base/supported_types.h"\n#include "media/base/test_data_util.h"')
        extra='''
TEST_F(FFmpegVideoDecoderTest, HevcSupportRequiresRegisteredDecoder) {
  const bool registered = avcodec_find_decoder(AV_CODEC_ID_HEVC) != nullptr;
  EXPECT_EQ(IsDecoderBuiltInVideoCodec(VideoCodec::kHEVC), registered);
  EXPECT_EQ(FFmpegVideoDecoder::IsCodecSupported(VideoCodec::kHEVC), registered);
  for (auto profile : {HEVCPROFILE_MAIN, HEVCPROFILE_MAIN10}) {
    VideoDecoderConfig config(VideoCodec::kHEVC, profile,
                              VideoDecoderConfig::AlphaMode::kIsOpaque,
                              VideoColorSpace(), kNoTransformation, kCodedSize,
                              kVisibleRect, kNaturalSize, EmptyExtraData(),
                              EncryptionScheme::kUnencrypted);
    InitializeWithConfigWithResult(config, registered);
  }
}

TEST_F(FFmpegVideoDecoderTest, HevcUnsupportedProfileRemainsRejected) {
  VideoDecoderConfig config(VideoCodec::kHEVC, HEVCPROFILE_REXT,
                            VideoDecoderConfig::AlphaMode::kIsOpaque,
                            VideoColorSpace(), kNoTransformation, kCodedSize,
                            kVisibleRect, kNaturalSize, EmptyExtraData(),
                            EncryptionScheme::kUnencrypted);
  InitializeWithConfigWithResult(config, false);
}

TEST_F(FFmpegVideoDecoderTest, HevcEncryptedInputRemainsRejected) {
  VideoDecoderConfig config(VideoCodec::kHEVC, HEVCPROFILE_MAIN,
                            VideoDecoderConfig::AlphaMode::kIsOpaque,
                            VideoColorSpace(), kNoTransformation, kCodedSize,
                            kVisibleRect, kNaturalSize, EmptyExtraData(),
                            EncryptionScheme::kCenc);
  InitializeWithConfigWithResult(config, false);
}

TEST_F(FFmpegVideoDecoderTest, HevcMain10DecodeAndReset) {
  VideoDecoderConfig config(VideoCodec::kHEVC, HEVCPROFILE_MAIN10,
                            VideoDecoderConfig::AlphaMode::kIsOpaque,
                            VideoColorSpace(), kNoTransformation, kCodedSize,
                            kVisibleRect, kNaturalSize, EmptyExtraData(),
                            EncryptionScheme::kUnencrypted);
  const bool registered = avcodec_find_decoder(AV_CODEC_ID_HEVC) != nullptr;
  RecordProperty("hevc_decoder_registered", registered);
  const bool initialized = InitializeWithConfigWithResult(config, registered);
  ASSERT_EQ(initialized, registered);
  if (!registered) {
    // This target configuration has no HEVC implementation to exercise.
    EXPECT_FALSE(FFmpegVideoDecoder::IsCodecSupported(VideoCodec::kHEVC));
    return;
  }
  auto frame = ReadTestDataFile("bear-320x180-10bit-frame-0.hevc");
  for (int pass = 0; pass < 2; ++pass) {
    ASSERT_TRUE(DecodeSingleFrame(frame).is_ok());
    ASSERT_EQ(output_frames_.size(), 1u);
    EXPECT_EQ(output_frames_.front()->format(), PIXEL_FORMAT_YUV420P10);
    EXPECT_EQ(output_frames_.front()->visible_rect(), kVisibleRect);
    output_frames_.clear();
    Reset();
  }
}

'''
        return replace_once(text,'TEST_F(FFmpegVideoDecoderTest, Initialize_Normal) {',extra+'TEST_F(FFmpegVideoDecoderTest, Initialize_Normal) {')
    raise AssertionError(path)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ffmpeg-delta',required=True,type=Path)
    p.add_argument('--scratch',required=True,type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'patches/0061-software-hevc-decoder.patch')
    a=p.parse_args(); data=json.loads(a.ffmpeg_delta.read_text())
    assert data['chromium_revision']==PIN and data['ffmpeg_revision']==FFMPEG
    a.scratch.mkdir(parents=True,exist_ok=False)
    changes=dict(data['changes'])
    for path in PATHS:
        url=f'https://chromium.googlesource.com/chromium/src/+/{PIN}/{path}?format=TEXT'
        before=base64.b64decode(urllib.request.urlopen(url,timeout=30).read()).decode()
        changes[path]={'before':before,'after':edits(path,before)}
    subprocess.run(['git','init','-q',str(a.scratch)],check=True)
    # No current earlier patch modifies these files. Keep this an explicit
    # check rather than assuming that independent source edits remain so.
    for name in (ROOT/'patches/series').read_text().splitlines():
        if not name or name.startswith('#') or name[:4]>='0061':continue
        text=(ROOT/'patches'/name).read_text()
        assert not any('+++ b/'+path+'\n' in text for path in changes), name+' overlaps candidate; rebase required'
    for path,change in changes.items():
        if change['before'] is not None:
            target=a.scratch/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(change['before'])
    patch=''
    for path,change in sorted(changes.items()):
        before=change['before']; after=change['after']
        patch+=f'diff --git a/{path} b/{path}\n'
        if before is None:patch+='new file mode 100644\n'
        patch+=''.join(difflib.unified_diff((before or '').splitlines(True),after.splitlines(True),
            fromfile='a/'+path if before is not None else '/dev/null',tofile='b/'+path))
    a.output.write_text(patch)
    subprocess.run(['git','apply','--check',str(a.output.resolve())],cwd=a.scratch,check=True)
    subprocess.run(['git','apply',str(a.output.resolve())],cwd=a.scratch,check=True)
    print(json.dumps({'patch':str(a.output),'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest(),
                      'files':len(changes),'added_objects':len(data['added_sources'])}))
if __name__=='__main__':main()
