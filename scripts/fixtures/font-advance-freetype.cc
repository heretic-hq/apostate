// Read-only diagnostic of FreeType's scaled and design-unit advances.
#include <ft2build.h>
#include FT_FREETYPE_H
#include <freetype/ftadvanc.h>
#include <cstdio>
#include <cstdlib>
#include <vector>

static std::vector<long> Outline(FT_Face face) {
  const auto& outline = face->glyph->outline;
  std::vector<long> result{outline.n_points, outline.n_contours};
  for (int i = 0; i < outline.n_points; ++i) {
    result.push_back(outline.points[i].x);
    result.push_back(outline.points[i].y);
    result.push_back(outline.tags[i]);
  }
  for (int i = 0; i < outline.n_contours; ++i) result.push_back(outline.contours[i]);
  return result;
}

int main(int argc, char** argv) {
  if (argc != 4) return 2;
  FT_Library library = nullptr;
  FT_Face face = nullptr;
  if (FT_Init_FreeType(&library) ||
      FT_New_Face(library, argv[1], std::strtol(argv[2], nullptr, 10), &face)) return 3;
  const auto glyph = FT_Get_Char_Index(face, std::strtoul(argv[3], nullptr, 10));
  if (!glyph || !face->units_per_EM) return 4;
  const double sizes[] = {0.5, 1, 1.5, 3, 4, 8, 12, 18, 24, 40, 63.5, 128};
  const auto flags = FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP;
  std::printf("{\"glyph\":%u,\"upem\":%u,\"rows\":[", glyph, face->units_per_EM);
  bool first = true;
  for (const auto size : sizes) {
    if (FT_Set_Char_Size(face, static_cast<FT_F26Dot6>(size * 64),
                         static_cast<FT_F26Dot6>(size * 64), 72, 72) ||
        FT_Load_Glyph(face, glyph, flags)) return 5;
    const double scaled = face->glyph->linearHoriAdvance / 65536.0;
    const auto outline = Outline(face);
    const auto originalHorizontal = face->glyph->linearHoriAdvance;
    const auto originalVertical = face->glyph->linearVertAdvance;
    const auto originalSize = face->size;
    FT_Fixed fastDesign = 0;
    if (FT_Get_Advance(face, glyph, FT_LOAD_NO_SCALE | FT_LOAD_IGNORE_TRANSFORM |
                       FT_ADVANCE_FLAG_FAST_ONLY, &fastDesign)) return 8;
    if (outline != Outline(face) || face->size != originalSize ||
        face->glyph->linearHoriAdvance != originalHorizontal ||
        face->glyph->linearVertAdvance != originalVertical) return 9;
    if (FT_Load_Glyph(face, glyph, flags | FT_LOAD_LINEAR_DESIGN)) return 6;
    const auto design = face->glyph->linearHoriAdvance;
    if (design != fastDesign) return 10;
    const double scaledDesign = double(design) * size / face->units_per_EM;
    // Same outline loading; only the linear advance representation differs.
    if (outline != Outline(face)) return 7;
    std::printf("%s{\"size\":%.17g,\"scaled\":%.17g,\"design\":%ld,"
                "\"scaledDesign\":%.17g,\"fastPreservesGlyph\":true}", first ? "" : ",", size, scaled,
                static_cast<long>(design), scaledDesign);
    first = false;
  }
  std::puts("]}");
  FT_Done_Face(face);
  FT_Done_FreeType(library);
}
