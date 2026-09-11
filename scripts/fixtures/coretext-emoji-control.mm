// Read-only CoreText diagnostic. No font registration, rendering or browser.
#import <Foundation/Foundation.h>
#import <CoreText/CoreText.h>
#import <CoreGraphics/CoreGraphics.h>

static NSString* Name(CTFontRef font, CFStringRef key) {
  CFStringRef value=CTFontCopyName(font,key);
  return value ? CFBridgingRelease(value) : @"";
}

static NSDictionary* Measure(CTFontRef font, NSString* mode, CGFloat requested) {
  UniChar text[2]={0xD83D,0xDD12}; CGGlyph glyphs[2]={0,0};
  bool mapped=CTFontGetGlyphsForCharacters(font,text,glyphs,2);
  CGGlyph glyph=glyphs[0]?glyphs[0]:glyphs[1];
  CGSize advance={}; CGRect box={};
  CTFontGetAdvancesForGlyphs(font,kCTFontOrientationHorizontal,&glyph,&advance,1);
  CTFontGetBoundingRectsForGlyphs(font,kCTFontOrientationHorizontal,&glyph,&box,1);
  CGFontRef graphics=CTFontCopyGraphicsFont(font,nullptr);int rawAdvance=0;
  CGFontGetGlyphAdvances(graphics,&glyph,1,&rawAdvance);
  CFTypeRef url=CTFontCopyAttribute(font,kCTFontURLAttribute);
  CFTypeRef optical=CTFontCopyAttribute(font,CFSTR("NSCTFontOpticalSizeAttribute"));
  CFTypeRef tracking=CTFontCopyAttribute(font,CFSTR("NSCTFontUnscaledTrackingAttribute"));
  NSDictionary* row=@{@"mode":mode,@"requestedSize":@(requested),@"size":@(CTFontGetSize(font)),
    @"family":Name(font,kCTFontFamilyNameKey),@"postscript":Name(font,kCTFontPostScriptNameKey),
    @"url":url?[(__bridge id)url description]:@"",@"opticalSize":optical?[(__bridge id)optical description]:@"",
    @"tracking":tracking?[(__bridge id)tracking description]:@"",@"mapped":@(mapped),
    @"glyphs":@[@(glyphs[0]),@(glyphs[1])],@"unitsPerEm":@(CGFontGetUnitsPerEm(graphics)),
    @"rawCGAdvance":@(rawAdvance),@"advance":@[@(advance.width),@(advance.height)],
    @"bounds":@[@(box.origin.x),@(box.origin.y),@(box.size.width),@(box.size.height)],
    @"ascent":@(CTFontGetAscent(font)),@"descent":@(CTFontGetDescent(font)),@"leading":@(CTFontGetLeading(font))};
  if(url)CFRelease(url);if(optical)CFRelease(optical);if(tracking)CFRelease(tracking);CGFontRelease(graphics);
  return row;
}

static CTFontRef ExactCopy(CTFontRef base, CGFloat size) {
  CFTypeRef optical=CTFontCopyAttribute(base,CFSTR("NSCTFontOpticalSizeAttribute"));
  double value=CTFontGetSize(base);
  if(optical && CFGetTypeID(optical)==CFNumberGetTypeID()){
    double candidate=0;if(CFNumberGetValue((CFNumberRef)optical,kCFNumberDoubleType,&candidate)&&candidate>0)value=candidate;
  }
  if(optical)CFRelease(optical);
  NSDictionary* attributes=@{@"NSCTFontOpticalSizeAttribute":@(value),@"NSCTFontUnscaledTrackingAttribute":@0};
  CTFontDescriptorRef descriptor=CTFontDescriptorCreateWithAttributes((__bridge CFDictionaryRef)attributes);
  CTFontRef font=CTFontCreateCopyWithAttributes(base,size,nullptr,descriptor);CFRelease(descriptor);return font;
}

int main(int argc,char** argv) {
  @autoreleasepool {
    if(argc!=2){fprintf(stderr,"usage: coretext-emoji-control font-file\n");return 2;}
    NSURL* url=[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
    NSArray* descriptors=CFBridgingRelease(CTFontManagerCreateFontDescriptorsFromURL((__bridge CFURLRef)url));
    NSMutableArray* rows=[NSMutableArray array];
    CFStringRef lock=CFSTR("🔒");CFRange range=CFRangeMake(0,CFStringGetLength(lock));
    for(NSNumber* value in @[@12,@14,@18,@24]){
      CGFloat size=value.doubleValue;
      for(NSString* name in @[@"Apple Color Emoji",@".Apple Color Emoji UI"]){
        CTFontRef font=CTFontCreateWithName((__bridge CFStringRef)name,size,nullptr);
        [rows addObject:Measure(font,[name stringByAppendingString:@"/named"],size)];
        CTFontRef copy=ExactCopy(font,size);[rows addObject:Measure(copy,[name stringByAppendingString:@"/exact-copy"],size)];
        CFRelease(copy);CFRelease(font);
      }
      for(NSUInteger i=0;i<descriptors.count;i++){
        CTFontRef font=CTFontCreateWithFontDescriptor((__bridge CTFontDescriptorRef)descriptors[i],size,nullptr);
        [rows addObject:Measure(font,[NSString stringWithFormat:@"file-descriptor/%lu",(unsigned long)i],size)];CFRelease(font);
      }
      for(NSString* kind in @[@"Arial",@"system-ui"]){
        CTFontRef base=[kind isEqualToString:@"Arial"]?CTFontCreateWithName(CFSTR("Arial"),size,nullptr):CTFontCreateUIFontForLanguage(kCTFontUIFontSystem,size,CFSTR("en"));
        CTFontRef fallback=CTFontCreateForString(base,lock,range);
        [rows addObject:Measure(fallback,[kind stringByAppendingString:@"/CTFontCreateForString"],size)];
        CFRelease(fallback);CFRelease(base);
      }
      CTFontRef base=CTFontCreateWithName(CFSTR("Apple Color Emoji"),12,nullptr);
      CTFontRef copy=ExactCopy(base,size);[rows addObject:Measure(copy,@"Apple Color Emoji/base12-exact-copy",size)];
      CFRelease(copy);CFRelease(base);
    }
    NSDictionary* result=@{@"diagnostic_only":@YES,@"corpus_admission":@NO,@"inputFile":url.path,@"rows":rows};
    NSError* error=nil;NSData* json=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:&error];
    if(!json){fprintf(stderr,"%s\n",error.description.UTF8String);return 1;}
    fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);
  }
  return 0;
}
