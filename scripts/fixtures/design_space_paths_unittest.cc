// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "base/base_paths.h"
#include "base/files/file_path.h"
#include "base/path_service.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkData.h"
#include "third_party/skia/include/core/SkFont.h"
#include "third_party/skia/include/core/SkFontArguments.h"
#include "third_party/skia/include/core/SkFontTypes.h"
#include "third_party/skia/include/core/SkPath.h"
#include "third_party/skia/include/core/SkStream.h"
#include "third_party/skia/include/core/SkTypeface.h"
#include "third_party/skia/src/core/SkAutoMalloc.h"
#include "third_party/skia/src/core/SkDescriptor.h"
#include "third_party/skia/src/core/SkFontPriv.h"
#include "third_party/skia/src/core/SkPtrRecorder.h"
#include "third_party/skia/src/core/SkReadBuffer.h"
#include "third_party/skia/src/core/SkScalerContext.h"
#include "third_party/skia/src/core/SkWriteBuffer.h"
#include "third_party/skia/src/ports/SkTypeface_FreeType.h"

#include <initializer_list>
#include <utility>

namespace {

class DesignSpacePathsTest : public testing::Test {
 protected:
  void SetUp() override {
    base::FilePath directory;
    ASSERT_TRUE(base::PathService::Get(base::DIR_EXE, &directory));
    const auto path = directory.AppendASCII("test_fonts")
                          .AppendASCII("DesignSpaceRoboto-Regular.ttf");
    auto data = SkData::MakeFromFileName(path.value().c_str());
    ASSERT_TRUE(data) << path.value();
    // Call the real FreeType factory unconditionally. No build-macro branch or
    // skip may turn missing FreeType coverage into a passing test.
    typeface_ = SkTypeface_FreeType::MakeFromStream(
        SkMemoryStream::Make(std::move(data)), SkFontArguments());
    ASSERT_TRUE(typeface_);
    ASSERT_EQ(typeface_->getUnitsPerEm(), 2048);
    glyph_ = typeface_->unicharToGlyph('A');
    ASSERT_NE(glyph_, 0);
  }

  SkFont RoundTrip(const SkFont& font) {
    auto typefaces = sk_make_sp<SkRefCntSet>();
    SkBinaryWriteBuffer writer({});
    writer.setTypefaceRecorder(typefaces);
    SkFontPriv::Flatten(font, writer);
    EXPECT_EQ(typefaces->count(), 1);
    SkAutoMalloc storage(writer.bytesWritten());
    writer.writeToMemory(storage.get());
    SkReadBuffer reader(storage.get(), writer.bytesWritten());
    sk_sp<SkTypeface> typeface = font.refTypeface();
    reader.setTypefaceArray(&typeface, 1);
    SkFont result;
    EXPECT_TRUE(SkFontPriv::Unflatten(&result, reader));
    return result;
  }

  sk_sp<SkTypeface> typeface_;
  SkGlyphID glyph_ = 0;
};

TEST_F(DesignSpacePathsTest, IdentitySerializationAndCacheDescriptor) {
  SkFont native(typeface_);
  EXPECT_FALSE(native.isDesignSpacePaths());
  for (float size : {1.5f, 40.f, 64.f, 128.f}) {
    SCOPED_TRACE(size);
    native.setSize(size);
    for (bool existing_flags : {false, true}) {
      native.setForceAutoHinting(existing_flags);
      native.setEmbeddedBitmaps(existing_flags);
      native.setSubpixel(existing_flags);
      native.setLinearMetrics(existing_flags);
      native.setEmbolden(existing_flags);
      native.setBaselineSnap(existing_flags);
      SkFont design = native;
      design.setDesignSpacePaths(true);
      EXPECT_NE(design, native);
      EXPECT_EQ(RoundTrip(native), native);
      EXPECT_EQ(RoundTrip(design), design);
      EXPECT_TRUE(RoundTrip(design).isDesignSpacePaths());
      SkScalerContextRec native_rec, design_rec;
      SkScalerContextEffects native_effects, design_effects;
      SkScalerContext::MakeRecAndEffectsFromFont(native, &native_rec,
                                               &native_effects);
      SkScalerContext::MakeRecAndEffectsFromFont(design, &design_rec,
                                               &design_effects);
      EXPECT_EQ(native_rec.fFlags & SkScalerContext::kDesignSpacePaths_Flag, 0);
      EXPECT_NE(design_rec.fFlags & SkScalerContext::kDesignSpacePaths_Flag, 0);
      auto native_descriptor = SkScalerContext::DescriptorGivenRecAndEffects(
          native_rec, native_effects);
      auto design_descriptor = SkScalerContext::DescriptorGivenRecAndEffects(
          design_rec, design_effects);
      EXPECT_NE(*native_descriptor, *design_descriptor);
      design.setDesignSpacePaths(false);
      EXPECT_EQ(design, native);
    }
  }
}

TEST_F(DesignSpacePathsTest, SyntheticBoldRetainsNativePath) {
  SkFont native(typeface_);
  native.setEmbolden(true);
  native.setHinting(SkFontHinting::kNone);
  for (float size : {1.5f, 18.f, 40.f, 63.5f, 128.f}) {
    SCOPED_TRACE(size);
    for (float scale : {0.005f, 0.5f, 1.f, 2.f, -1.f}) {
      SCOPED_TRACE(scale);
      for (float skew : {-0.75f, 0.f, 0.5f}) {
        SCOPED_TRACE(skew);
        native.setSize(size);
        native.setScaleX(scale);
        native.setSkewX(skew);
        SkFont design = native;
        design.setDesignSpacePaths(true);
        auto expected = native.getPath(glyph_);
        ASSERT_TRUE(expected.has_value());
        EXPECT_FALSE(expected->isEmpty());
        EXPECT_EQ(design.getPath(glyph_), expected);
        EXPECT_EQ(native.getPath(glyph_), expected);
        EXPECT_EQ(design.getPath(glyph_), expected);
      }
    }
  }
}

TEST_F(DesignSpacePathsTest, DesignOutlineHasExactUnroundedBounds) {
  // The pinned static Roboto glyph A has glyf bounds [28,0,1309,1456]
  // in 2048 units/em. At the canonical64px path size, scaleX33/32
  // makes x coordinates exact multiples of1/1024, finer than FT26.6.
  SkFont native(typeface_, 64.f);
  native.setHinting(SkFontHinting::kNone);
  native.setEmbeddedBitmaps(false);
  native.setScaleX(33.f / 32.f);
  SkFont design = native;
  design.setDesignSpacePaths(true);
  const SkRect expected = SkRect::MakeLTRB(
      28.f * 33.f / 1024.f, -1456.f / 32.f,
      1309.f * 33.f / 1024.f, 0.f);
  auto native_path = native.getPath(glyph_);
  auto design_path = design.getPath(glyph_);
  ASSERT_TRUE(native_path.has_value());
  ASSERT_TRUE(design_path.has_value());
  EXPECT_FALSE(design_path->isEmpty());
  EXPECT_EQ(design_path->getBounds(), expected);
  // This fails if the new flag is ignored or both requests share one strike.
  EXPECT_NE(*design_path, *native_path);
  EXPECT_EQ(native.getPath(glyph_), native_path);
  EXPECT_EQ(design.getPath(glyph_), design_path);
}

}  // namespace
