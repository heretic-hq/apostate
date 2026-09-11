// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// Standalone diagnostic. Reads an operator-supplied font; no browser or network.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

#include "base/command_line.h"
#include "base/containers/span.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/json/json_writer.h"
#include "base/no_destructor.h"
#include "base/strings/string_number_conversions.h"
#include "base/values.h"
#include "base/test/test_discardable_memory_allocator.h"
#include "skia/ext/font_utils.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkCanvas.h"
#include "third_party/skia/include/core/SkColorSpace.h"
#include "third_party/skia/include/core/SkData.h"
#include "third_party/skia/include/core/SkFont.h"
#include "third_party/skia/include/core/SkFontArguments.h"
#include "third_party/skia/include/core/SkFontMetrics.h"
#include "third_party/skia/include/core/SkFontTypes.h"
#include "third_party/skia/include/core/SkGraphics.h"
#include "third_party/skia/include/core/SkPaint.h"
#include "third_party/skia/include/core/SkPath.h"
#include "third_party/skia/include/core/SkStream.h"
#include "third_party/skia/include/core/SkString.h"
#include "third_party/skia/include/core/SkTypeface.h"
#include "third_party/skia/include/ports/SkTypeface_fontations.h"
#include "third_party/skia/src/core/SkArenaAlloc.h"
#include "third_party/skia/src/core/SkDescriptor.h"
#include "third_party/skia/src/core/SkGlyph.h"
#include "third_party/skia/src/core/SkScalerContext.h"

namespace {
base::ListValue Numbers(std::initializer_list<double> values) {
  base::ListValue result;
  for (double value : values) {
    result.Append(value);
  }
  return result;
}
base::ListValue Rectangle(const SkRect& rect) {
  return Numbers({rect.x(), rect.y(), rect.width(), rect.height()});
}
// Empirical reconstruction arm only. These functions change the diagnostic's
// drawing request; they do not alter Fontations or the selected font bytes.
double EffectiveSize(double size) {
  return size <= 16 ? size * 1.25 : size < 24 ? size * .5 + 12 : size + .0001;
}
double Baseline(double size) {
  return size <= 16 ? -size / 4 : size < 24 ? size / 8 - 6 : -size / 8;
}
base::DictValue ScalerSnapshot(const SkFont& font, SkGlyphID id, float scale) {
  SkScalerContextRec rec;
  SkScalerContextEffects effects;
  SkScalerContext::MakeRecAndEffectsFromFont(font, &rec, &effects);
  rec.fPost2x2[0][0] = scale;
  rec.fPost2x2[1][1] = scale;
  auto descriptor = SkScalerContext::DescriptorGivenRecAndEffects(rec, effects);
  auto scaler = font.getTypeface()->createScalerContext(effects, &*descriptor);
  base::DictValue result;
  result.Set("ok", !!scaler);
  if (!scaler) {
    return result;
  }
  SkArenaAlloc arena(4096);
  auto glyph = scaler->makeGlyph(SkPackedGlyphID(id), &arena);
  result.Set("advance", Numbers({glyph.advanceX(), glyph.advanceY()}));
  result.Set("bounds", Rectangle(glyph.rect()));
  result.Set("maskFormat", static_cast<int>(glyph.maskFormat()));
  // Separate scaler instance: this explicit path request does not modify the
  // canvas strike used for the bitmap render below.
  scaler->getPath(glyph, &arena);
  result.Set("explicitPathPresent", glyph.path() != nullptr);
  if (glyph.path()) {
    result.Set("explicitPathBounds", Rectangle(glyph.path()->getBounds()));
  }
  return result;
}
}  // namespace

int main(int argc, char** argv) {
  base::CommandLine::Init(argc, argv);
  const auto* command = base::CommandLine::ForCurrentProcess();
  const auto input = command->GetSwitchValuePath("font-file");
  const auto output = command->GetSwitchValuePath("output-dir");
  int index = 0;
  if (input.empty() || output.empty() || base::PathExists(output) ||
      (command->HasSwitch("font-index") &&
       !base::StringToInt(command->GetSwitchValueASCII("font-index"), &index)) ||
      index < 0 || index > 8) {
    std::cerr << "Provide --font-file and a new --output-dir; optional --font-index=0..8\n";
    return 2;
  }
  auto stream = SkStream::MakeFromFile(input.value().c_str());
  if (!stream || stream->getLength() > 256u * 1024u * 1024u) {
    std::cerr << "Font unavailable or larger than 256 MiB\n";
    return 2;
  }
  auto face = SkTypeface_Make_Fontations(
      std::move(stream), SkFontArguments().setCollectionIndex(index));
  if (!face || !base::CreateDirectory(output)) {
    std::cerr << "Fontations load or output creation failed\n";
    return 2;
  }
  static base::NoDestructor<base::TestDiscardableMemoryAllocator> allocator;
  base::DiscardableMemoryAllocator::SetInstance(allocator.get());
  SkGraphics::Init();
  skia::InitializeFontRendering();
  SkString ps;
  face->getPostScriptName(&ps);
  base::DictValue document;
  document.Set("diagnosticOnly", true);
  document.Set("corpusAdmission", false);
  document.Set("postscript", ps.c_str());
  document.Set("upem", face->getUnitsPerEm());
  document.Set("collectionIndex", index);
  document.Set("factory", "SkTypeface_Make_Fontations; operator file input");
  document.Set("format", "sRGB premultiplied RGBA8888; tightly packed top-to-bottom rows");
  document.Set("fontRenderingInitialization", "skia::InitializeFontRendering (PNG decoder registration)");
  document.Set("discardableAllocator", "base::TestDiscardableMemoryAllocator; process lifetime");
  document.Set("hinting", "none");
  document.Set("antialias", true);
  document.Set("subpixel", true);
  document.Set("linearMetrics", true);
  document.Set("embeddedBitmaps", true);
  document.Set("fontSmoothing", false);
  document.Set("sampling", "unmodified Fontations bitmap implementation");
  base::ListValue rows;
  constexpr std::array<int, 8> sizes = {8, 12, 14, 16, 20, 24, 32, 48};
  constexpr std::array<SkUnichar, 5> scalars = {0x1f512, 0x1f600, 0x1f680, 0x2600, 0x2764};
  for (const std::string mode : {"file_exact", "file_flagged", "file_reconstruction", "file_device_reconstruction"}) {
    for (int size : sizes) {
      for (SkUnichar scalar : scalars) {
        const SkGlyphID glyph = face->unicharToGlyph(scalar);
        if (!glyph) {
          std::cerr << "Required diagnostic glyph missing\n";
          return 3;
        }
        for (int scale : {1, 2}) {
          const bool reconstruction = mode == "file_reconstruction";
          const bool device_reconstruction = mode == "file_device_reconstruction";
          const double requested_size = device_reconstruction ? EffectiveSize(size * scale) / scale :
              reconstruction ? EffectiveSize(size) : size;
          const double y_shift = device_reconstruction ? Baseline(size * scale) / scale :
              reconstruction ? Baseline(size) : 0;
          SkFont font(face, static_cast<float>(requested_size));
          font.setHinting(SkFontHinting::kNone);
          font.setEdging(SkFont::Edging::kAntiAlias);
          font.setSubpixel(true);
          font.setLinearMetrics(true);
          font.setEmbeddedBitmaps(true);
          font.setDesignSpacePaths(mode != "file_exact");
          constexpr int logical_width = 256, logical_height = 192;
          const int width = logical_width * scale, height = logical_height * scale;
          std::vector<uint8_t> pixels(static_cast<size_t>(width) * height * 4);
          SkBitmap bitmap;
          if (!bitmap.installPixels(SkImageInfo::Make(width, height, kRGBA_8888_SkColorType,
                  kPremul_SkAlphaType, SkColorSpace::MakeSRGB()), pixels.data(), width * 4)) {
            return 4;
          }
          SkCanvas canvas(bitmap);
          canvas.clear(SK_ColorTRANSPARENT);
          canvas.scale(scale, scale);
          const std::array<SkGlyphID, 1> ids = {glyph};
          const std::array<SkPoint, 1> positions = {SkPoint::Make(0, 0)};
          SkPaint paint;
          paint.setAntiAlias(true);
          // Native control has CG y-up origin(64,64) on a192px-high canvas.
          canvas.drawGlyphs(ids, positions,
              SkPoint::Make(64, static_cast<float>(logical_height - 64 - y_shift)), font, paint);
          int left = width, right = -1, top = height, bottom = -1;
          for (int y = 0; y < height; ++y) {
            for (int x = 0; x < width; ++x) {
              if (pixels[(static_cast<size_t>(y) * width + x) * 4 + 3]) {
                left = std::min(left, x);
                right = std::max(right, x);
                top = std::min(top, y);
                bottom = std::max(bottom, y);
              }
            }
          }
          if (right < left) {
            std::cerr << "Empty bitmap render\n";
            return 5;
          }
          const std::string name = mode + "-u" + base::NumberToString(scalar) + "-s" +
              base::NumberToString(size) + "-x" + base::NumberToString(scale) + ".rgba";
          if (!base::WriteFile(output.AppendASCII(name), base::span(pixels))) {
            return 6;
          }
          base::DictValue row;
          row.Set("mode", mode);
          row.Set("size", size);
          row.Set("scale", scale);
          row.Set("scalar", scalar);
          row.Set("glyph", glyph);
          row.Set("requestedSizeDouble", requested_size);
          row.Set("actualSkFontSizeFloat", font.getSize());
          row.Set("baselineCG", y_shift);
          row.Set("advance", font.getWidth(glyph));
          row.Set("bounds", Rectangle(font.getBounds(glyph, nullptr)));
          const auto path = font.getPath(glyph);
          row.Set("publicPathPresent", path.has_value());
          if (path) {
            row.Set("publicPathBounds", Rectangle(path->getBounds()));
          }
          SkFontMetrics metrics;
          font.getMetrics(&metrics);
          row.Set("fontMetrics", Numbers({-metrics.fAscent, metrics.fDescent, metrics.fLeading}));
          row.Set("scaler", ScalerSnapshot(font, glyph, scale));
          row.Set("file", name);
          row.Set("pixelSize", Numbers({static_cast<double>(width), static_cast<double>(height)}));
          row.Set("originInCGCoordinates", Numbers({64, 64 + y_shift}));
          row.Set("alphaBoundsInMemoryRows", Numbers({static_cast<double>(left), static_cast<double>(top),
              static_cast<double>(right-left+1), static_cast<double>(bottom-top+1)}));
          rows.Append(std::move(row));
        }
      }
    }
  }
  document.Set("rows", std::move(rows));
  const auto json = base::WriteJson(document);
  if (!json || !base::WriteFile(output.AppendASCII("result.json"), *json)) {
    return 7;
  }
  std::cout << "rows=320 rgbaFiles=320\n";
  return 0;
}
