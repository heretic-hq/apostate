#!/usr/bin/env python3
"""Exercise the actual 0049 FreeType state helper against pinned FreeType.

Uses a private work directory, never the Chromium checkout. Cases are JSON
objects with file/index/codepoint/category keys. Font files are read only.
This validates extraction/state/outline behavior, not integrated Skia V1 or
CoreText equivalence. The helper is extracted from the supplied modified source.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import textwrap


PIN = "656cb777798fa420a13faba3758779e9ed6c4798"

HARNESS = r'''
#include <ft2build.h>
#include <freetype/freetype.h>
#include <freetype/ftsizes.h>
#include <freetype/ftoutln.h>
#include <freetype/ftmm.h>
#include <freetype/tttables.h>
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include "design-helper.h"
#include "design-branch.h"

uint64_t outlineHash(FT_Face face) {
    const auto& outline = face->glyph->outline;
    uint64_t hash = 1469598103934665603ULL;
    auto mix = [&](uint64_t value) { hash = (hash ^ value) * 1099511628211ULL; };
    mix(outline.n_points); mix(outline.n_contours);
    for (int i = 0; i < outline.n_points; ++i) {
        mix(outline.points[i].x); mix(outline.points[i].y); mix(outline.tags[i]);
    }
    for (int i = 0; i < outline.n_contours; ++i) mix(outline.contours[i]);
    return hash;
}

int main(int argc, char** argv) {
    assert(argc == 5);
    const std::string category(argv[4]);
    FT_Library library = nullptr;
    assert(FT_Init_FreeType(&library) == 0);
    FT_Face face = nullptr;
    const int loaded = FT_New_Face(library, argv[1], std::stol(argv[2]), &face);
    if (category == "unsupported") {
        assert(loaded != 0);
        std::cout << "{\"unavailable_load_error\":" << loaded << "}\n";
        FT_Done_FreeType(library);
        return 0;
    }
    if (category == "color-hybrid") {
        assert(loaded == 0);
        assert(FT_HAS_COLOR(face) || FT_HAS_SVG(face) || FT_HAS_SBIX(face));
        assert(!actualDesignBranch(face, SkScalerContext::kDesignSpacePaths_Flag, true));
        FT_ULong glyfLength = 0;
        assert(FT_Load_Sfnt_Table(face, FT_MAKE_TAG('g', 'l', 'y', 'f'), 0,
                                  nullptr, &glyfLength) == 0 && glyfLength > 0);
        std::cout << "{\"color_guard_bypassed\":true,\"has_glyf\":true,\"has_color\":"
                  << (FT_HAS_COLOR(face) ? "true" : "false")
                  << ",\"has_svg\":" << (FT_HAS_SVG(face) ? "true" : "false")
                  << ",\"has_sbix\":" << (FT_HAS_SBIX(face) ? "true" : "false")
                  << ",\"scalable\":" << (FT_IS_SCALABLE(face) ? "true" : "false") << "}\n";
        FT_Done_Face(face);
        FT_Done_FreeType(library);
        return 0;
    }
    assert(loaded == 0 && FT_IS_SCALABLE(face) && !FT_IS_TRICKY(face));
    const FT_UInt glyph = FT_Get_Char_Index(face, std::stoul(argv[3]));
    assert(glyph != 0);
    const FT_Int32 normalFlags = FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP;
    std::vector<FT_Fixed> coords;
    FT_MM_Var* variation = nullptr;
    bool variationChangedOutline = false;
    if (category == "variable" || category == "cff2") {
        assert(FT_Get_MM_Var(face, &variation) == 0);
        coords.resize(variation->num_axis);
        for (unsigned i = 0; i < coords.size(); ++i) {
            coords[i] = variation->axis[i].minimum +
                static_cast<FT_Fixed>((variation->axis[i].maximum - variation->axis[i].minimum) * 0.37137);
        }
        uint64_t defaultOutline;
        {
            ScopedFTDesignSpacePath scoped(face);
            assert(scoped.load(glyph, normalFlags));
            defaultOutline = outlineHash(face);
        }
        assert(FT_Set_Var_Design_Coordinates(face, coords.size(), coords.data()) == 0);
        {
            ScopedFTDesignSpacePath scoped(face);
            assert(scoped.load(glyph, normalFlags));
            variationChangedOutline = outlineHash(face) != defaultOutline;
            assert(variationChangedOutline);
        }
    }
    if (category == "composite") {
        assert(FT_Load_Glyph(face, glyph, normalFlags | FT_LOAD_NO_RECURSE) == 0);
        assert(face->glyph->format == FT_GLYPH_FORMAT_COMPOSITE);
    }
    const FT_Matrix transforms[] = {
        {65536, 0, 0, 65536}, {0, -65536, 65536, 0}, {49152, -16384, 32768, 81920}
    };
    unsigned cases = 0;
    unsigned boldFallbackCases = 0;
    assert(actualDesignBranch(face, SkScalerContext::kDesignSpacePaths_Flag, true));
    assert(!actualDesignBranch(face, 0, true));
    assert(!actualDesignBranch(face, SkScalerContext::kDesignSpacePaths_Flag, false));
    assert(!actualDesignBranch(face, SkScalerContext::kDesignSpacePaths_Flag |
                                   SkScalerContext::kEmbolden_Flag, true));
    bool sawFractionalDesignPoint = false;
    FT_BBox designBox{};
    for (const auto& matrix : transforms) {
        assert(FT_Set_Char_Size(face, 64 * 64, 64 * 64, 72, 72) == 0);
        FT_Matrix mutableMatrix = matrix;
        FT_Vector delta{7 * 64, -3 * 64};
        FT_Set_Transform(face, &mutableMatrix, &delta);
        FT_Size originalSize = face->size;
        assert(FT_Load_Glyph(face, glyph, normalFlags) == 0);
        const uint64_t originalOutline = outlineHash(face);
        const FT_Fixed originalAdvance = face->glyph->linearHoriAdvance;
        auto nativeBold = [&]() {
            assert(FT_Load_Glyph(face, glyph, normalFlags) == 0);
            const FT_Pos strength = FT_MulFix(face->units_per_EM,
                face->size->metrics.y_scale) / 24;
            assert(FT_Outline_Embolden(&face->glyph->outline, strength) == 0);
            return outlineHash(face);
        };
        const uint64_t boldWithoutPolicy = nativeBold();
        // The compiled condition is extracted verbatim from generatePath.
        // With synthetic bold it must bypass the design helper entirely and
        // retain the native load-transform-embolden order.
        assert(!actualDesignBranch(face, SkScalerContext::kDesignSpacePaths_Flag |
                                        SkScalerContext::kEmbolden_Flag, true));
        const uint64_t boldWithPolicy = nativeBold();
        assert(boldWithoutPolicy == boldWithPolicy);
        ++boldFallbackCases;
        for (int operation = 0; operation < 3; ++operation) {
            {
                ScopedFTDesignSpacePath scoped(face);
                const bool ok = scoped.load(operation == 2 ? face->num_glyphs + 100 : glyph,
                                           normalFlags | FT_LOAD_NO_RECURSE);
                if (operation == 2) {
                    assert(!ok);
                } else {
                    assert(ok && face->glyph->format == FT_GLYPH_FORMAT_OUTLINE);
                    assert(FT_Outline_Check(&face->glyph->outline) == 0);
                    assert(face->glyph->outline.n_points > 0);
                    assert(face->size->metrics.x_ppem == face->units_per_EM);
                    for (int i = 0; i < face->glyph->outline.n_points; ++i) {
                        const auto point = face->glyph->outline.points[i];
                        sawFractionalDesignPoint |= point.x % 64 || point.y % 64;
                        for (float size : {1.5f, 3.f, 4.f, 8.f, 12.f, 18.f, 24.f, 40.f, 63.5f, 128.f}) {
                            // Same design->canonical->requested scaling order as the Skia caller.
                            const float canonicalX = (point.x / 64.f) * (64.f / face->units_per_EM);
                            const float canonicalY = (-point.y / 64.f) * (64.f / face->units_per_EM);
                            const float x = (matrix.xx / 65536.f * canonicalX -
                                             matrix.xy / 65536.f * canonicalY) * (size / 64.f);
                            const float y = (-matrix.yx / 65536.f * canonicalX +
                                              matrix.yy / 65536.f * canonicalY) * (size / 64.f);
                            assert(std::isfinite(x) && std::isfinite(y));
                        }
                    }
                    FT_Outline_Get_CBox(&face->glyph->outline, &designBox);
                    if (category == "songti") {
                        assert(designBox.xMin == 52 * 64 && designBox.xMax == 947 * 64);
                        assert(designBox.yMin == -166 * 64 && designBox.yMax == 767 * 64);
                    }
                    if (operation == 1) {
                        // Real FT synthesis mutates only the current glyph slot, then the
                        // next normal load must still reproduce its untouched outline.
                        const FT_Pos strength = FT_MulFix(face->units_per_EM,
                            face->size->metrics.y_scale) / 24;
                        assert(FT_Outline_Embolden(&face->glyph->outline, strength) == 0);
                    }
                }
            }
            FT_Matrix restored;
            FT_Vector restoredDelta;
            FT_Get_Transform(face, &restored, &restoredDelta);
            assert(face->size == originalSize);
            assert(face->size->metrics.x_ppem == 64 && face->size->metrics.y_ppem == 64);
            assert(restored.xx == matrix.xx && restored.xy == matrix.xy &&
                   restored.yx == matrix.yx && restored.yy == matrix.yy);
            assert(restoredDelta.x == delta.x && restoredDelta.y == delta.y);
            if (!coords.empty()) {
                std::vector<FT_Fixed> after(coords.size());
                assert(FT_Get_Var_Design_Coordinates(face, after.size(), after.data()) == 0);
                assert(after == coords);
            }
            assert(FT_Load_Glyph(face, glyph, normalFlags) == 0);
            assert(outlineHash(face) == originalOutline);
            assert(face->glyph->linearHoriAdvance == originalAdvance);
            ++cases;
        }
    }
    // gvar preserves fractional design points. The pinned unhinted CFF driver
    // first emits integer font units (psft.c:284), then scales in cffgload.c:708;
    // CFF2 therefore cannot promise the same fractional precision.
    if (category == "variable") assert(sawFractionalDesignPoint);
    std::cout << "{\"state_cases\":" << cases << ",\"glyph\":" << glyph
              << ",\"bold_fallback_cases\":" << boldFallbackCases
              << ",\"variation_changed_outline\":" << (variationChangedOutline ? "true" : "false")
              << ",\"upem\":" << face->units_per_EM
              << ",\"fractional_design_points\":" << (sawFractionalDesignPoint ? "true" : "false")
              << ",\"design_box_26_6\":[" << designBox.xMin << "," << designBox.yMin
              << "," << designBox.xMax << "," << designBox.yMax << "]}\n";
    if (variation) FT_Done_MM_Var(library, variation);
    FT_Done_Face(face);
    FT_Done_FreeType(library);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="modified SkFontHost_FreeType.cpp")
    parser.add_argument("--scaler-header", type=Path, required=True, help="matching SkScalerContext.h")
    parser.add_argument("--work", type=Path, required=True, help="private reusable test work directory")
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = args.source.read_text()
    start = source.index("class ScopedFTDesignSpacePath {")
    end = source.index("\n};", start) + len("\n};")
    helper = source[start:end]
    (work / "design-helper.h").write_text(helper + "\n")
    begin = source.index("    if ((fRec.fFlags & SkScalerContext::kDesignSpacePaths_Flag)",
                         source.index("SkScalerContext_FreeType::generatePath")) + len("    if (")
    end = source.index(") {\n        ScopedFTDesignSpacePath designSpace", begin)
    condition = source[begin:end]
    header = args.scaler_header.read_text()
    constants = []
    for name in ["kDesignSpacePaths_Flag", "kEmbolden_Flag"]:
        value = re.search(r"\b" + name + r"\s*=\s*(0x[0-9a-fA-F]+)", header).group(1)
        constants.append(f"static constexpr unsigned {name} = {value};")
    branch = "struct SkScalerContext {" + "".join(constants) + "};\n"
    branch += "enum class SkFontHinting { kNone, kNormal };\n"
    branch += "bool actualDesignBranch(FT_Face fFace, unsigned flags, bool unhinted) {\n"
    branch += ("struct Rec { unsigned fFlags; bool unhinted; "
               "SkFontHinting getHinting() const { return unhinted ? SkFontHinting::kNone : SkFontHinting::kNormal; } };\n")
    branch += "Rec fRec{flags, unhinted};\nreturn " + condition + ";\n}\n"
    (work / "design-branch.h").write_text(branch)
    (work / "design-path-check.cc").write_text(textwrap.dedent(HARNESS))
    ft = work / "freetype"
    if not ft.exists():
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                        "https://chromium.googlesource.com/chromium/src/third_party/freetype2.git", str(ft)], check=True)
        subprocess.run(["git", "-C", str(ft), "checkout", "--quiet", "--detach", PIN], check=True)
    actual = subprocess.check_output(["git", "-C", str(ft), "rev-parse", "HEAD"], text=True).strip()
    if actual != PIN or subprocess.check_output(["git", "-C", str(ft), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("FreeType test source must be pristine and pinned")
    build = work / "build"
    with (work / "build.log").open("w") as log:
        subprocess.run(["cmake", "-S", str(ft), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                        "-DBUILD_SHARED_LIBS=OFF", *["-DFT_DISABLE_" + dependency + "=TRUE"
                        for dependency in ["ZLIB", "BZIP2", "PNG", "HARFBUZZ", "BROTLI", "HVF"]]],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(["nice", "-n", "19", "cmake", "--build", str(build), "--parallel", "1"],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    library = build / "libfreetype.a"
    binary = work / "design-path-check"
    subprocess.run(["c++", "-std=c++17", "-O0", "-g", "-I" + str(build / "include"),
                    "-I" + str(ft / "include"), str(work / "design-path-check.cc"),
                    str(library), "-lm", "-o", str(binary)], check=True)
    results = []
    for case in json.loads(args.cases.read_text()):
        path = Path(case["file"])
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if case.get("expected_sha256") and actual_hash != case["expected_sha256"]:
            raise RuntimeError("font input hash does not match the supplied case pin")
        command = [str(binary), str(path), str(case["index"]), str(case["codepoint"]), case["category"]]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        result = {**case, "font_sha256": actual_hash,
                  "exit_code": run.returncode, "stderr": run.stderr}
        result["observed"] = json.loads(run.stdout) if run.returncode == 0 else run.stdout
        if "expected_scalable" in case:
            result["scalability_matches_pin"] = (run.returncode == 0 and
                result["observed"].get("scalable") == case["expected_scalable"])
        results.append(result)
    receipt = {"diagnostic_only": True, "corpus_admission": False, "freetype_revision": PIN,
               "helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
               "branch_sha256": hashlib.sha256(condition.encode()).hexdigest(),
               "branch_extracted_from_candidate": True,
               "integrated_skia_tested": False, "cases": results,
               "passed": all(item["exit_code"] == 0 and item.get("scalability_matches_pin", True) for item in results)}
    (work / "results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"results": str(work / "results.json"), "passed": receipt["passed"]}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
