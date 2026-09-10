#!/usr/bin/env python3
"""Generate the profile generic-font producer patch in a temporary checkout."""
import base64
import difflib
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "79460ebecaa5625e57a5fb679a735659e73dc687"
BROWSER = "chrome/browser/chrome_content_browser_client.cc"
PATHS = ["base/apostate/profile.h", "base/apostate/profile.cc", BROWSER]


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Expected unique anchor: {old[:100]}")
    return source.replace(old, new, 1)


def edit(path, source):
    if path.endswith("profile.h"):
        source = once(source, "  struct SpeechVoice {", """  const std::map<std::string, std::string>& generic_font_family_map() const {
    return generic_font_family_map_;
  }

  struct SpeechVoice {""")
        return once(source, "  bool has_speech_voice_filter_ = false;", """  std::map<std::string, std::string> generic_font_family_map_;
  bool has_speech_voice_filter_ = false;""")
    if path.endswith("profile.cc"):
        return once(source, '  if (const DictValue* speech = dict->FindDict("speech")) {', """  if (const DictValue* fonts = dict->FindDict("fonts")) {
    if (const Value* raw_map = fonts->Find("generic_family_map")) {
      const DictValue* families = raw_map->GetIfDict();
      if (!families) {
        LOG(ERROR) << "apostate: fonts.generic_family_map must be an object";
        return nullptr;
      }
      for (auto [generic, value] : *families) {
        const std::string* family = value.GetIfString();
        if ((generic != "serif" && generic != "sans-serif" &&
             generic != "monospace" && generic != "cursive" &&
             generic != "fantasy" && generic != "math") ||
            !family || family->empty() || family->size() > 256 ||
            family->find('\\0') != std::string::npos) {
          LOG(ERROR) << "apostate: invalid generic font identity";
          return nullptr;
        }
        profile->generic_font_family_map_.emplace(generic, *family);
      }
    }
  }

  if (const DictValue* speech = dict->FindDict("speech")) {""")
    if '#include "base/apostate/profile.h"' not in source:
        source = once(source, '#include "base/base_switches.h"',
                      '#include "base/apostate/profile.h"\n#include "base/base_switches.h"')
    return once(source, '  web_prefs->default_font_size =\n', """  // Generic font identities are browser preferences, not CSS aliases. Use the
  // existing WebPreferences delivery to every renderer and preserve unmeasured
  // script-specific maps. The profile loader is the sole source of this input.
  if (const auto* device = base::apostate::Profile::Get()) {
    for (const auto& [generic, family] : device->generic_font_family_map()) {
      auto* map = &web_prefs->serif_font_family_map;
      if (generic == "sans-serif")
        map = &web_prefs->sans_serif_font_family_map;
      else if (generic == "monospace")
        map = &web_prefs->fixed_font_family_map;
      else if (generic == "cursive")
        map = &web_prefs->cursive_font_family_map;
      else if (generic == "fantasy")
        map = &web_prefs->fantasy_font_family_map;
      else if (generic == "math")
        map = &web_prefs->math_font_family_map;
      (*map)[blink::web_pref::kCommonScript] = base::UTF8ToUTF16(family);
    }
  }

  web_prefs->default_font_size =
""")


def main():
    with urllib.request.urlopen(
            f"https://chromium.googlesource.com/chromium/src/+/{REVISION}/{BROWSER}?format=TEXT",
            timeout=45) as response:
        original = base64.b64decode(response.read()).decode()
    with tempfile.TemporaryDirectory(prefix="apostate-generic-fonts-") as temp:
        scratch = Path(temp)
        source = scratch / BROWSER
        source.parent.mkdir(parents=True)
        source.write_text(original)
        for name in (ROOT / "patches/series").read_text().splitlines():
            name = name.split("#", 1)[0].strip()
            if not name or int(name[:4]) >= 47:
                continue
            subprocess.run(["git", "apply", *[f"--include={p}" for p in PATHS],
                            str(ROOT / "patches" / name)], cwd=scratch, check=True)
        result = ["""Subject: [PATCH] fonts: supply captured generic identities through WebPreferences

Generic CSS families are selected from browser preference maps before
fontconfig sees the requested face. A fontconfig serif alias therefore cannot
replace Chrome's existing Times New Roman preference with the Mac's Times.

Read fonts.generic_family_map through the existing profile loader and set the
common-script maps where Chrome produces WebPreferences. Every renderer uses
the existing preference delivery. Named families, unknown generic settings and
unmeasured per-script maps retain their own behavior. No accessor is replaced.

Derivation requires a detected named control matching every captured metric
and the native preferred identity from Chromium's platform locale resources.
Widths alone do not identify a font. Standard/default and math remain absent
when unmeasured. This does not reconstruct a platform's character fallback.

References: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json and
resources/fingerprints/raw/unlabelled-20260910T140818Z.json, fonts.metrics and
fonts.detected; Chromium79460ebecaa5625e57a5fb679a735659e73dc687
chrome/app/resources/locale_settings_{mac,win}.grd and
chrome/browser/chrome_content_browser_client.cc:4635-4674.
Ledger: fonts.generic-families. V1 and browser controls remain required.

"""]
        for path in PATHS:
            before = (scratch / path).read_text()
            after = edit(path, before)
            result.append(f"diff --git a/{path} b/{path}\n")
            result.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                               fromfile=f"a/{path}", tofile=f"b/{path}"))
        patch = scratch / "candidate.patch"
        patch.write_text("".join(result))
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=scratch, check=True)
        destination = ROOT / "patches/0047-generic-font-preferences.patch"
        destination.write_text(patch.read_text())
        print(destination)


if __name__ == "__main__":
    main()
