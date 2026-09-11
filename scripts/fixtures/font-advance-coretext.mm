// Read-only exact-file CoreText advance diagnostic. Does not register fonts.
#import <Foundation/Foundation.h>
#import <CoreText/CoreText.h>

int main(int argc, char** argv) {
  @autoreleasepool {
    if (argc != 4) return 2;
    NSURL* url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
    NSString* wanted = [NSString stringWithUTF8String:argv[2]];
    UniChar codepoint = static_cast<UniChar>(strtoul(argv[3], nullptr, 10));
    NSArray* descriptors = CFBridgingRelease(
        CTFontManagerCreateFontDescriptorsFromURL((__bridge CFURLRef)url));
    NSMutableArray* rows = [NSMutableArray array];
    for (id descriptor in descriptors) {
      CTFontRef probe = CTFontCreateWithFontDescriptor(
          (__bridge CTFontDescriptorRef)descriptor, 12, nullptr);
      NSString* name = CFBridgingRelease(CTFontCopyPostScriptName(probe));
      CFRelease(probe);
      if (![name isEqualToString:wanted]) continue;
      for (NSNumber* size in @[@0.5,@1,@1.5,@3,@4,@8,@12,@18,@24,@40,@63.5,@128]) {
        CTFontRef font = CTFontCreateWithFontDescriptor(
            (__bridge CTFontDescriptorRef)descriptor, size.doubleValue, nullptr);
        CGGlyph glyph = 0;
        if (!CTFontGetGlyphsForCharacters(font, &codepoint, &glyph, 1) || !glyph) return 4;
        CGSize advance{};
        CTFontGetAdvancesForGlyphs(font, kCTFontOrientationHorizontal, &glyph, &advance, 1);
        [rows addObject:@{@"size":size,@"glyph":@(glyph),
                         @"upem":@(CTFontGetUnitsPerEm(font)),@"advance":@(advance.width)}];
        CFRelease(font);
      }
    }
    if (rows.count != 12) return 5;
    NSDictionary* result = @{@"diagnostic_only":@YES,@"corpus_admission":@NO,
                              @"postscript":wanted,@"rows":rows};
    NSData* data = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:nil];
    if (!data) return 6;
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
  }
}
