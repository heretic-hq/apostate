/* Diagnostic only: real pinned FFmpeg CPU decoding, no encoder or hardware API. */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "libavcodec/avcodec.h"
#include "libavformat/avformat.h"
#include "libavutil/imgutils.h"
#include "libavutil/md5.h"
#include "libavutil/pixdesc.h"

static enum AVPixelFormat software_format(struct AVCodecContext *context,
                                          const enum AVPixelFormat *formats) {
  (void)context;
  for (const enum AVPixelFormat *p = formats; *p != AV_PIX_FMT_NONE; ++p)
    if (*p == AV_PIX_FMT_YUV420P || *p == AV_PIX_FMT_YUV420P10LE) return *p;
  return AV_PIX_FMT_NONE;
}

static int emit_frame(AVFrame *frame, unsigned *count, int *width, int *height, int *depth) {
  if (frame->hw_frames_ctx || av_frame_apply_cropping(frame, 0) < 0) return -1;
  if (frame->format != AV_PIX_FMT_YUV420P && frame->format != AV_PIX_FMT_YUV420P10LE) return -1;
  const int bytes_per_sample = frame->format == AV_PIX_FMT_YUV420P ? 1 : 2;
  if (!*count) {
    *width = frame->width; *height = frame->height; *depth = bytes_per_sample == 1 ? 8 : 10;
  }
  if (*width != frame->width || *height != frame->height) return -1;
  struct AVMD5 *md5 = av_md5_alloc();
  if (!md5) return -1;
  av_md5_init(md5);
  for (int plane = 0; plane < 3; ++plane) {
    const int w = plane ? (frame->width + 1) / 2 : frame->width;
    const int h = plane ? (frame->height + 1) / 2 : frame->height;
    const int row_bytes = w * bytes_per_sample;
    if (frame->linesize[plane] < row_bytes || !frame->data[plane]) { av_free(md5); return -1; }
    for (int row = 0; row < h; ++row)
      av_md5_update(md5, frame->data[plane] + (size_t)row * frame->linesize[plane], row_bytes);
  }
  uint8_t digest[16];
  av_md5_final(md5, digest); av_free(md5);
  if (*count) printf(",");
  printf("\"");
  for (int i = 0; i < 16; ++i) printf("%02x", digest[i]);
  printf("\"");
  ++*count;
  return 0;
}

static int receive(AVCodecContext *codec, AVFrame *frame, unsigned *count,
                   int *width, int *height, int *depth, int draining) {
  for (;;) {
    const int status = avcodec_receive_frame(codec, frame);
    if (status == AVERROR_EOF) return draining ? 0 : -1;
    if (status == AVERROR(EAGAIN)) return draining ? -1 : 0;
    if (status < 0) return status;
    const int result = emit_frame(frame, count, width, height, depth);
    av_frame_unref(frame);
    if (result < 0) return result;
  }
}

int main(int argc, char **argv) {
  if (argc != 3 || (strcmp(argv[2], "hevc") && strcmp(argv[2], "mp4"))) return 2;
  av_max_alloc(256 * 1024 * 1024);
  const AVCodec *decoder = avcodec_find_decoder(AV_CODEC_ID_HEVC);
  if (!decoder || strcmp(decoder->name, "hevc")) return 3;
  AVFormatContext *format = avformat_alloc_context();
  AVCodecContext *codec = NULL;
  AVPacket *packet = av_packet_alloc();
  AVFrame *frame = av_frame_alloc();
  int result = 1;
  if (!format || !packet || !frame) goto done;
  format->protocol_whitelist = av_strdup("file");
  const AVInputFormat *forced = !strcmp(argv[2], "hevc") ? av_find_input_format("hevc") : NULL;
  if (avformat_open_input(&format, argv[1], forced, NULL) < 0 ||
      avformat_find_stream_info(format, NULL) < 0) goto done;
  const int stream = av_find_best_stream(format, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
  if (stream < 0 || format->streams[stream]->codecpar->codec_id != AV_CODEC_ID_HEVC) goto done;
  codec = avcodec_alloc_context3(decoder);
  if (!codec || avcodec_parameters_to_context(codec, format->streams[stream]->codecpar) < 0) goto done;
  codec->thread_count = 2;
  codec->thread_type = FF_THREAD_FRAME | FF_THREAD_SLICE;
  codec->get_format = software_format;
  if (avcodec_open2(codec, decoder, NULL) < 0) goto done;
  printf("{\"diagnostic_only\":true,\"corpus_admission\":false,\"decoder\":\"hevc\",\"software_only\":true,\"passes\":[");
  for (int pass = 0; pass < 2; ++pass) {
    if (pass) {
      const int flags = !strcmp(argv[2], "hevc") ? AVSEEK_FLAG_BYTE : AVSEEK_FLAG_BACKWARD;
      if (av_seek_frame(format, flags == AVSEEK_FLAG_BYTE ? -1 : stream, 0, flags) < 0) goto done;
      avcodec_flush_buffers(codec);
      printf(",");
    }
    unsigned count = 0;
    int width = 0, height = 0, depth = 0;
    printf("{\"frame_md5\":[");
    int status;
    while ((status = av_read_frame(format, packet)) >= 0) {
      if (packet->stream_index == stream) {
        if (avcodec_send_packet(codec, packet) < 0 ||
            receive(codec, frame, &count, &width, &height, &depth, 0) < 0) goto done;
      }
      av_packet_unref(packet);
    }
    if (status != AVERROR_EOF || avcodec_send_packet(codec, NULL) < 0 ||
        receive(codec, frame, &count, &width, &height, &depth, 1) < 0 || count == 0) goto done;
    printf("],\"frames\":%u,\"width\":%d,\"height\":%d,\"bit_depth\":%d}", count, width, height, depth);
  }
  printf("],\"seek_and_flush_completed\":true}\n");
  result = 0;
done:
  av_frame_free(&frame); av_packet_free(&packet);
  avcodec_free_context(&codec); avformat_close_input(&format);
  if (result) fprintf(stderr, "HEVC decode/seek/reset failed\n");
  return result;
}
