// Native CoreText diagnostic. This is not a Chromium capture or corpus row.
import Foundation
import CoreText

var rows: [[String: Any]] = []
for family in ["Arial", "Helvetica", "Times", "Times New Roman", "Menlo"] {
    for size in [18.0, 40.0] {
        let original = CTFontCreateWithName(family as CFString, size, nil)
        let sample = "字" as CFString
        let fallback = CTFontCreateForString(original, sample, CFRange(location: 0, length: 1))
        var character: UniChar = 0x5b57
        var glyph: CGGlyph = 0
        let found = CTFontGetGlyphsForCharacters(fallback, &character, &glyph, 1)
        var advance = CGSize.zero
        CTFontGetAdvancesForGlyphs(fallback, .horizontal, &glyph, &advance, 1)
        let bounds = CTFontGetBoundingRectsForGlyphs(fallback, .horizontal, &glyph, nil, 1)
        let url = CTFontCopyAttribute(fallback, kCTFontURLAttribute) as? URL
        rows.append([
            "requestedFamily": family, "size": size,
            "selectedFamily": CTFontCopyFamilyName(original) as String,
            "selectedPostScriptName": CTFontCopyPostScriptName(original) as String,
            "fallbackFamily": CTFontCopyFamilyName(fallback) as String,
            "fallbackPostScriptName": CTFontCopyPostScriptName(fallback) as String,
            "fallbackURL": url?.path ?? "", "glyphFound": found, "glyph": Int(glyph),
            "advance": advance.width,
            "bounds": [bounds.minX, bounds.minY, bounds.maxX, bounds.maxY]
        ])
    }
}
let result: [String: Any] = ["diagnosticOnly": true, "corpusAdmission": false,
                            "api": "CTFontCreateForString", "rows": rows]
let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
