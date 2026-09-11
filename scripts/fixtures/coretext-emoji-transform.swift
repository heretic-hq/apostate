// Read-only native oracle for the supplied system emoji face. No registration.
import Foundation
import CoreText
import CoreGraphics

var rows: [[String: Any]] = []
for name in ["named", "exact-copy", "base12-copy", "system-fallback"] {
    for size in stride(from: 8.0, through: 32.0, by: 0.5) {
        var font = CTFontCreateWithName("Apple Color Emoji" as CFString,
                                       name == "base12-copy" ? 12 : size, nil)
        if name == "system-fallback" {
            let base = CTFontCreateUIFontForLanguage(.system, size, "en" as CFString)!
            font = CTFontCreateForString(base, "🔒" as CFString, CFRange(location: 0, length: 2))
        } else if name != "named" {
            let optical = CTFontCopyAttribute(font, "NSCTFontOpticalSizeAttribute" as CFString)
            let attributes: [String: Any] = ["NSCTFontOpticalSizeAttribute": optical ?? CTFontGetSize(font),
                                              "NSCTFontUnscaledTrackingAttribute": 0]
            let descriptor = CTFontDescriptorCreateWithAttributes(attributes as CFDictionary)
            font = CTFontCreateCopyWithAttributes(font, size, nil, descriptor)
        }
        let chars: [UniChar] = [0xD83D, 0xDD12]
        var glyphs: [CGGlyph] = [0, 0]
        let mapped = CTFontGetGlyphsForCharacters(font, chars, &glyphs, 2)
        var glyph = glyphs.first(where: { $0 != 0 }) ?? 0
        var advance = CGSize.zero
        var bounds = CGRect.zero
        CTFontGetAdvancesForGlyphs(font, .horizontal, &glyph, &advance, 1)
        CTFontGetBoundingRectsForGlyphs(font, .horizontal, &glyph, &bounds, 1)
        let matrix = CTFontGetMatrix(font)
        let cg = CTFontCopyGraphicsFont(font, nil)
        var rawAdvance: Int32 = 0
        let rawAvailable = cg.getGlyphAdvances(glyphs: &glyph, count: 1, advances: &rawAdvance)
        let attributeKeys = ["NSCTFontOpticalSizeAttribute", "NSCTFontUnscaledTrackingAttribute"]
        var attributes: [String: String] = [:]
        for key in attributeKeys {
            if let value = CTFontCopyAttribute(font, key as CFString) {
                attributes[key] = String(describing: value)
            }
        }
        rows.append(["name": name, "size": size, "mapped": mapped, "glyph": glyph,
                     "postscript": CTFontCopyPostScriptName(font) as String,
                     "matrix": [matrix.a, matrix.b, matrix.c, matrix.d, matrix.tx, matrix.ty],
                     "advance": [advance.width, advance.height], "rawAdvance": rawAdvance,
                     "rawAvailable": rawAvailable, "upem": cg.unitsPerEm,
                     "bounds": [bounds.minX, bounds.minY, bounds.width, bounds.height],
                     "attributes": attributes])
    }
}
let data = try JSONSerialization.data(withJSONObject: ["diagnostic_only": true,
    "corpus_admission": false, "rows": rows], options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data([10]))
