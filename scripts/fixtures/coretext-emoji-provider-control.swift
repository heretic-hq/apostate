// Bounded native system-provider diagnostic. No font registration or browser.
import Foundation
import CoreText
import CoreGraphics
import ImageIO
import CryptoKit

let out = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let fontURL = URL(fileURLWithPath: "/System/Library/Fonts/Apple Color Emoji.ttc")
let descriptors = CTFontManagerCreateFontDescriptorsFromURL(fontURL as CFURL) as? [CTFontDescriptor] ?? []
guard let fileDescriptor = descriptors.first(where: {
    (CTFontDescriptorCopyAttribute($0, kCTFontNameAttribute) as? String) == "AppleColorEmoji"
}) else { fatalError("AppleColorEmoji file descriptor missing") }

func exact(_ font: CTFont, _ size: CGFloat) -> CTFont {
    let optical = CTFontCopyAttribute(font, "NSCTFontOpticalSizeAttribute" as CFString)
    let attributes: [String: Any] = [
        "NSCTFontOpticalSizeAttribute": optical ?? CTFontGetSize(font),
        "NSCTFontUnscaledTrackingAttribute": 0
    ]
    return CTFontCreateCopyWithAttributes(font, size, nil,
        CTFontDescriptorCreateWithAttributes(attributes as CFDictionary))
}
func rectangle(_ rect: CGRect) -> Any {
    let values = [rect.minX, rect.minY, rect.width, rect.height]
    return values.allSatisfy { $0.isFinite } ? values : NSNull()
}
func sha(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}
func render(_ font: CTFont, _ glyph: CGGlyph, _ mode: String, _ scalar: UInt32,
            _ size: Double, _ scale: Int, _ deviceSized: Bool = false) -> [String: Any] {
    let width = 256 * scale, height = 192 * scale
    let yShift = mode == "file_reconstruction" ? providerBaseline(size) : 0
    let drawFont = deviceSized ? exact(font, size * Double(scale)) : font
    let drawScale = deviceSized ? 1 : scale
    let originMultiplier = deviceSized ? scale : 1
    let renderMode = deviceSized ? mode + "_device" : mode
    var pixels = [UInt8](repeating: 0, count: width * height * 4)
    var saved = false
    let name = "\(renderMode)-u\(String(scalar, radix:16))-s\(size)-x\(scale).png"
    let ok = pixels.withUnsafeMutableBytes { bytes -> Bool in
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: bytes.baseAddress, width: width, height: height,
                bitsPerComponent: 8, bytesPerRow: width * 4, space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)
        else { return false }
        context.scaleBy(x: CGFloat(drawScale), y: CGFloat(drawScale))
        context.textMatrix = .identity
        context.setAllowsFontSmoothing(false)
        context.setShouldSmoothFonts(false)
        context.setShouldAntialias(true)
        context.interpolationQuality = .high
        var gid = glyph
        var origin = CGPoint(x: Double(64 * originMultiplier), y: (64 + yShift) * Double(originMultiplier))
        CTFontDrawGlyphs(drawFont, &gid, &origin, 1, context)
        if let image = context.makeImage(),
           let destination = CGImageDestinationCreateWithURL(out.appendingPathComponent(name) as CFURL,
                                                            "public.png" as CFString, 1, nil) {
            CGImageDestinationAddImage(destination, image, nil)
            saved = CGImageDestinationFinalize(destination)
        }
        return true
    }
    var left = width, right = -1, top = height, bottom = -1
    if ok {
        for y in 0..<height { for x in 0..<width {
            if pixels[(y * width + x) * 4 + 3] != 0 {
                left = min(left, x); right = max(right, x)
                top = min(top, y); bottom = max(bottom, y)
            }
        }}
    }
    let box: Any = right >= left ? [left, top, right-left+1, bottom-top+1] : NSNull()
    var drawGlyph = glyph
    var drawAdvance = CGSize.zero, drawBounds = CGRect.zero
    CTFontGetAdvancesForGlyphs(drawFont, .horizontal, &drawGlyph, &drawAdvance, 1)
    CTFontGetBoundingRectsForGlyphs(drawFont, .horizontal, &drawGlyph, &drawBounds, 1)
    let rawName = name + ".rgba"
    let rawSaved = (try? Data(pixels).write(to: out.appendingPathComponent(rawName))) != nil
    return ["ok": ok, "saved": saved, "file": name, "rawFile": rawName,
            "rawSaved": rawSaved, "scale": scale,
            "renderModel": deviceSized ? "CTFont-at-device-size" : "logical-CTFont-scaled-context",
            "drawFontSize": CTFontGetSize(drawFont),
            "drawFontAdvance": [drawAdvance.width, drawAdvance.height],
            "drawFontBounds": rectangle(drawBounds),
            "pixelSize": [width, height], "originInCGCoordinates": [Double(64 * originMultiplier), (64 + yShift) * Double(originMultiplier)],
            "alphaBoundsInMemoryRows": box, "rgbaSHA256": sha(Data(pixels)),
            "format": "sRGB, premultiplied-last RGBA, byteOrder32Big, default CG coordinates",
            "interpolation": "high", "fontSmoothing": false, "antialias": true]
}

// Diagnostic hypothesis only: compare actual images against a reconstruction.
func providerEm(_ size: Double) -> Double {
    size <= 16 ? size * 1.25 : (size < 24 ? size * 0.5 + 12 : size + 0.0001)
}
func providerBaseline(_ size: Double) -> Double {
    size <= 16 ? -size / 4 : (size < 24 ? size / 8 - 6 : -size / 8)
}
let grid = Array(stride(from: 8.0, through: 32.0, by: 0.5))
let heldout: [Double] = [1.5, 4, 6, 7.25, 8.125, 11.75, 15.875, 16.125,
                         17.125, 23.875, 24.125, 32.25, 40, 48, 64, 96]
let sizes = Array(Set(grid + heldout)).sorted()
let scalars: [UInt32] = [0x1F512, 0x1F600, 0x1F680, 0x1F44D, 0x1F525,
                          0x2600, 0x2764, 0x20, 0x31]
let renderSizes: Set<Double> = [8, 12, 14, 16, 20, 24, 32, 48]
let renderScalars: Set<UInt32> = [0x1F512, 0x1F600, 0x1F680, 0x2600, 0x2764]
var rows: [[String: Any]] = []
var renderCount = 0
for mode in ["named_exact", "file_exact", "ui_exact", "named_bare", "file_reconstruction"] {
    for size in sizes { for scalar in scalars {
        let text = String(UnicodeScalar(scalar)!)
        let font: CTFont
        switch mode {
        case "file_reconstruction":
            let effectiveSize = providerEm(size)
            font = exact(CTFontCreateWithFontDescriptor(fileDescriptor, effectiveSize, nil), effectiveSize)
        case "file_exact":
            font = exact(CTFontCreateWithFontDescriptor(fileDescriptor, size, nil), size)
        case "ui_exact":
            let ui = CTFontCreateUIFontForLanguage(.system, size, "en" as CFString)!
            let fallback = CTFontCreateForString(ui, text as CFString,
                                                 CFRange(location: 0, length: text.utf16.count))
            font = exact(fallback, size)
        case "named_bare":
            font = CTFontCreateWithName("Apple Color Emoji" as CFString, size, nil)
        default:
            font = exact(CTFontCreateWithName("Apple Color Emoji" as CFString, size, nil), size)
        }
        let chars = Array(text.utf16)
        var glyphs = [CGGlyph](repeating: 0, count: chars.count)
        let mapped = CTFontGetGlyphsForCharacters(font, chars, &glyphs, chars.count)
        var glyph = glyphs.first(where: { $0 != 0 }) ?? 0
        var advance = CGSize.zero, box = CGRect.zero
        CTFontGetAdvancesForGlyphs(font, .horizontal, &glyph, &advance, 1)
        CTFontGetBoundingRectsForGlyphs(font, .horizontal, &glyph, &box, 1)
        let graphicsFont = CTFontCopyGraphicsFont(font, nil)
        var rawAdvance: Int32 = 0, rawBox = CGRect.zero
        let rawAdvanceOK = graphicsFont.getGlyphAdvances(glyphs: &glyph, count: 1, advances: &rawAdvance)
        let rawBoundsOK = graphicsFont.getGlyphBBoxes(glyphs: &glyph, count: 1, bboxes: &rawBox)
        let path = CTFontCreatePathForGlyph(font, glyph, nil)
        let matrix = CTFontGetMatrix(font)
        var attributes: [String: String] = [:]
        for key in ["NSCTFontOpticalSizeAttribute", "NSCTFontUnscaledTrackingAttribute"] {
            if let value = CTFontCopyAttribute(font, key as CFString) { attributes[key] = String(describing: value) }
        }
        var row: [String: Any] = ["mode": mode, "size": size, "heldout": heldout.contains(size),
            "scalar": scalar, "text": text, "mapped": mapped, "glyph": glyph,
            "postscript": CTFontCopyPostScriptName(font) as String, "ctSize": CTFontGetSize(font),
            "upem": graphicsFont.unitsPerEm, "advance": [advance.width, advance.height],
            "bounds": rectangle(box), "rawAdvance": rawAdvance, "rawAdvanceOK": rawAdvanceOK,
            "rawBounds": rectangle(rawBox), "rawBoundsOK": rawBoundsOK,
            "hasCTPath": path != nil, "pathBounds": path.map { rectangle($0.boundingBoxOfPath) } ?? NSNull(),
            "fontMetrics": [CTFontGetAscent(font), CTFontGetDescent(font), CTFontGetLeading(font)],
            "matrix": [matrix.a, matrix.b, matrix.c, matrix.d, matrix.tx, matrix.ty],
            "attributes": attributes]
        if mode != "named_bare" && renderSizes.contains(size) && renderScalars.contains(scalar) && mapped {
            row["renders"] = [render(font, glyph, mode, scalar, size, 1),
                              render(font, glyph, mode, scalar, size, 2)]
            renderCount += 2
            if mode == "named_exact" {
                row["deviceSizeRenders"] = [render(font, glyph, mode, scalar, size, 1, true),
                                           render(font, glyph, mode, scalar, size, 2, true)]
                renderCount += 2
            }
        }
        rows.append(row)
    }}
}
let document: [String: Any] = ["diagnosticOnly": true, "corpusAdmission": false,
    "fontFile": fontURL.path, "fontFileSHA256": sha(try Data(contentsOf: fontURL)),
    "gridSizes": grid, "heldoutSizes": heldout, "rows": rows]
let data = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
try data.write(to: out.appendingPathComponent("result.json"))
print("rows=\(rows.count) renders=\(renderCount)")
