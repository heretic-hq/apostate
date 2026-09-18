#!/usr/bin/env bash
# Install everything Chromium's Windows build needs that the runner image does
# not already carry, in one step, before any money is spent on a sync.
#
# Two installers, because the prerequisites come from two products:
#
#   1. Visual Studio components, through the VS installer's `modify --add`.
#      build/WINDOWS_VS_COMPONENTS lists them and the files that prove each one,
#      with the source for every entry. The one the image lacks is
#      Microsoft.VisualStudio.Component.VC.ATL: ATL ships only with Visual
#      Studio, is a separate component from the C++ toolset, and is what
#      "'atldef.h' file not found" means. A compiler and an SDK that are both
#      complete are not evidence that ATL is there, which is why the older
#      `vswhere -requires ...VC.Tools.x86.x64` check passed while the build
#      failed 33,797 edges in.
#
#   2. The Windows SDK's "Debugging Tools for Windows" feature, through the
#      pinned winsdksetup.exe. build/vs_toolchain.py treats
#      <SDK>/Debuggers/x64/dbghelp.dll as mandatory and raises without it.
#
# Both phases are idempotent: with everything present this does nothing, exits
# 0, and costs no download, so an image that gains a component later stops
# paying for it automatically.
#
# NEITHER PHASE TRUSTS AN EXIT CODE. The VS installer's documented codes
# include 3010, "Operation completed successfully, but install requires reboot
# before it can be used", and a hosted runner cannot take a reboot. Headers and
# import libraries need no registration -- clang-cl only reads them -- so a 3010
# is expected to be benign here, but "expected" is not a check. Each phase
# re-tests the same files it tested before installing, and those files are the
# only success signal. If ATL ever turns out to need the reboot it asked for,
# this fails naming the component instead of handing the gate a broken toolchain.
#
# PROVENANCE OF THAT CLAIM, because it matters which half is measured. The
# behaviour of this script on 0, 3010, 1641, each documented failure code and an
# undocumented one was demonstrated against a STUB installer that wrote a chosen
# exit code and optionally created the files, not against a real
# reboot-required install. So "a 3010 whose files did not land fails naming the
# component" is proven; "a real 3010 from this installer leaves usable headers"
# is reasoned from the fact that headers need no registration, and is exactly
# what the re-check exists to catch if the reasoning is wrong.
#
# It reports what it found either way -- the VS install path, the resolved MSVC
# toolset, the SDK version directories, and per component whether it was already
# there or installed by this run -- because every Windows failure on this project
# so far was diagnosed from one line of incidental log output, and this script
# runs on a 2 vCPU probe runner where logging is free.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) die "provision-windows-toolchain.sh only runs on Windows" ;;
esac

pin() {
  local file="$REPO_ROOT/build/$1" value
  [ -f "$file" ] || die "missing build/$1"
  value="$(tr -d '[:space:]' < "$file")"
  [ -n "$value" ] || die "build/$1 is empty"
  printf '%s' "$value"
}

# One scratch directory and one trap for both phases. Two traps would mean the
# second silently replaced the first and phase 1's temporary files outlived the
# run.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# ---------------------------------------------------------------------------
# Phase 1: Visual Studio components
# ---------------------------------------------------------------------------

say "visual studio install: ${vs2022_install:-<none resolved>}"
[ -n "${vs2022_install:-}" ] ||
  die "no Visual Studio 2022 install; scripts/lib.sh could not resolve one through vswhere.
Chromium needs Microsoft.VisualStudio.Component.VC.Tools.x86.x64 at minimum."

vs_unix="$(cygpath -u "$vs2022_install" 2>/dev/null || printf '%s' "$vs2022_install")"

# Every toolset present, not only the selected one: a second toolset appearing
# would change which directory the build resolves, and this listing is how that
# would be noticed.
say "MSVC toolsets under $vs_unix/VC/Tools/MSVC"
for _t in "$vs_unix"/VC/Tools/MSVC/*; do
  [ -d "$_t" ] || { printf '  (none)\n'; break; }
  printf '  %s\n' "$(basename "$_t")"
done

toolset="$(windows_msvc_toolset_root || true)"
[ -n "$toolset" ] ||
  die "no VC/Tools/MSVC/14.* toolset under $vs_unix.
Install Microsoft.VisualStudio.Component.VC.Tools.x86.x64."
say "selected toolset: $(basename "$toolset") (highest, matching build/vs_toolchain.py FindVCComponentRoot)"

# Whether the component payloads have to come off the network. Hosted images
# install Build Tools with --nocache, so this directory is normally absent or
# near-empty and every --add is a download from Microsoft. Reported rather than
# assumed, because it is the difference between a per-job network dependency and
# a local extract.
vs_cache="/c/ProgramData/Microsoft/VisualStudio/Packages"
if [ -d "$vs_cache" ]; then
  say "VS package cache present: $vs_cache ($(du -sm "$vs_cache" 2>/dev/null | cut -f1) MB)"
else
  say "VS package cache absent ($vs_cache); component payloads come from the network"
fi

# Which components are missing, by the probes the pin file names. A component is
# missing when ANY of its probes is absent: half an install is not an install.
missing=()
say "required Visual Studio components (build/WINDOWS_VS_COMPONENTS)"
while IFS=' ' read -r component probe; do
  [ -n "$component" ] || continue
  if [ -e "$toolset/$probe" ]; then
    printf '  present  %s  (%s)\n' "$probe" "$component"
  else
    printf '  MISSING  %s  (%s)\n' "$probe" "$component"
    case " ${missing[*]-} " in
      *" $component "*) ;;
      *) missing+=("$component") ;;
    esac
  fi
done < <(windows_vs_component_probes)

if [ "${#missing[@]}" -eq 0 ]; then
  say "all required Visual Studio components already installed"
else
  say "installing ${#missing[@]} missing component(s): ${missing[*]}"

  # Locate the installer rather than hardcoding it. Deriving it from the
  # resolved install is the reliable route: the installer always sits beside the
  # product roots, at <...>/Microsoft Visual Studio/Installer, so an image that
  # puts Visual Studio somewhere unexpected is still handled. The canonical
  # locations follow as fallbacks. setup.exe is the name Microsoft's own docs
  # use; vs_installer.exe is the same launcher under its other name.
  installer=""
  for _dir in \
    "$(dirname "$(dirname "$vs_unix")")/Installer" \
    "/c/Program Files (x86)/Microsoft Visual Studio/Installer" \
    "/c/Program Files/Microsoft Visual Studio/Installer"; do
    for _exe in setup.exe vs_installer.exe; do
      if [ -f "$_dir/$_exe" ]; then installer="$_dir/$_exe"; break 2; fi
    done
  done
  [ -n "$installer" ] ||
    die "no Visual Studio installer found; cannot add ${missing[*]}.
Searched for setup.exe and vs_installer.exe beside $vs_unix and in both
Program Files trees. Without it the components must be baked into the image."
  say "installer: $installer"

  # --channelId is documented as required for modify alongside --installPath.
  # vswhere reports the installed instance's own channel, so this cannot select
  # a different one by accident; omitted if vswhere has no answer rather than
  # guessed.
  vswhere="/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
  channel=""
  if [ -x "$vswhere" ]; then
    channel="$("$vswhere" -latest -products '*' -version '[17.0,18.0)' \
      -property channelId 2>/dev/null | tr -d '\r')"
  fi
  say "channel: ${channel:-<unset, omitting --channelId>}"

  # Driven from PowerShell rather than straight from bash for two reasons.
  # MSYS rewrites anything argument-shaped that contains a slash, and
  # --installPath is a Windows path with spaces. And the exit code has to
  # survive: bash sees only the low 8 bits of a process exit status, so 3010
  # would arrive as 194 and -1073720687 as noise. PowerShell writes the real
  # 32-bit value to a file and this reads it back.
  ps1="$work/add-components.ps1"
  code_file="$work/exitcode"
  cat > "$ps1" <<'PS1'
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$InstallPath,
  [Parameter(Mandatory = $true)][string]$CodeFile,
  [string]$ChannelId = '',
  [Parameter(Mandatory = $true)][string]$Components
)
$ErrorActionPreference = 'Stop'

# Start-Process joins -ArgumentList with spaces and quotes NOTHING, so an array
# containing "C:\Program Files (x86)\..." arrives at the installer as
# --installPath C:\Program, followed by Files and (x86)\Microsoft... as stray
# arguments. Measured: that is exactly how the first real run failed, exit 1
# after 7 seconds, with the unquoted path visible in this script's own echo.
# Quote here and pass one pre-joined string.
function Quote-Arg([string]$value) {
  if ($value -match '[\s"]') { return '"' + ($value -replace '"', '\"') + '"' }
  return $value
}

$argv = @('modify', '--installPath', (Quote-Arg $InstallPath))
if ($ChannelId -ne '') { $argv += @('--channelId', (Quote-Arg $ChannelId)) }
# Semicolon-separated rather than a PowerShell array parameter: powershell -File
# passes arguments as literal strings, so "A,B" would bind as one component id
# named "A,B" and the installer would reject it. Splitting here is unambiguous.
foreach ($c in ($Components -split ';' | Where-Object { $_ -ne '' })) {
  $argv += @('--add', (Quote-Arg $c))
}
# --quiet: no UI on a headless runner. --norestart: never reboot the runner out
# from under the job; a reboot-required result is reported as 3010 instead.
# --nocache: delete the payloads after installing, because Windows is the
# target with the least free disk and the packages are not needed again.
$argv += @('--quiet', '--norestart', '--nocache')
$commandLine = $argv -join ' '

# The installer's own logs are the only account of why it failed, and they are
# written to %TEMP% on a runner that stops existing when the job ends. Note the
# start time so only this run's logs get printed.
$started = Get-Date

Write-Host ('running: "{0}" {1}' -f $Installer, $commandLine)
$clock = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $Installer -ArgumentList $commandLine -Wait -PassThru
$code = $proc.ExitCode

# The installer does not support --wait: "The --wait parameter can only be
# passed into the bootstrapper; the installer (setup.exe) doesn't support it."
# setup.exe hands the work to a separate engine, so -Wait can return while the
# install is still going. Wait for the installer processes to clear as well,
# bounded so a stuck installer fails the step instead of the job timeout.
$deadline = (Get-Date).AddMinutes(30)
$engines = @('setup', 'vs_installer', 'vs_installershell', 'vs_installerservice')
while ((Get-Date) -lt $deadline) {
  $live = @($engines | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue })
  if ($live.Count -eq 0) { break }
  Write-Host ('  waiting for {0} installer process(es) at {1:N0}s' -f $live.Count, $clock.Elapsed.TotalSeconds)
  Start-Sleep -Seconds 10
}
$clock.Stop()
Write-Host ('installer exit code {0} after {1:N0}s' -f $code, $clock.Elapsed.TotalSeconds)

if ($code -ne 0 -and $code -ne 3010 -and $code -ne 1641) {
  $logs = @(Get-ChildItem -Path $env:TEMP -Filter 'dd_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-5) } |
    Sort-Object LastWriteTime)
  if ($logs.Count -eq 0) {
    Write-Host 'no dd_*.log was written; the installer failed before it started logging'
  }
  foreach ($log in $logs) {
    Write-Host ('----- {0} (last 60 lines) -----' -f $log.FullName)
    Get-Content -Path $log.FullName -Tail 60 -ErrorAction SilentlyContinue |
      ForEach-Object { Write-Host $_ }
  }
}

Set-Content -Path $CodeFile -Value ([string]$code) -NoNewline
exit 0
PS1

  ps_args=(-NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$ps1")"
    -Installer "$(cygpath -w "$installer")"
    -InstallPath "$vs2022_install"
    -CodeFile "$(cygpath -w "$code_file")")
  [ -z "$channel" ] || ps_args+=(-ChannelId "$channel")
  ps_args+=(-Components "$(IFS=';'; printf '%s' "${missing[*]}")")

  # The script itself always exits 0 and writes the installer's real code to a
  # file; a non-zero status here means PowerShell could not run it at all.
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' powershell "${ps_args[@]}" ||
    die "could not run the Visual Studio installer through PowerShell"

  rc="$(tr -d '[:space:]' < "$code_file" 2>/dev/null || true)"
  # Documented at
  # learn.microsoft.com/visualstudio/install/use-command-line-parameters-to-install-visual-studio
  # ("Error codes"). Only two of these are successes, and 3010 is one of them:
  # treating it as a failure would fail a run that installed everything asked
  # for. The file re-check below is what actually decides.
  case "$rc" in
    0) say "installer reported success" ;;
    3010) warn "installer reported 3010: installed, reboot required before use" ;;
    1641) warn "installer reported 1641: installed, and it initiated a reboot despite --norestart" ;;
    740) die "installer exit 740: elevation required" ;;
    1001) die "installer exit 1001: a Visual Studio installer process is already running" ;;
    1003) die "installer exit 1003: Visual Studio is in use" ;;
    1602|5004) die "installer exit $rc: operation was cancelled" ;;
    1618) die "installer exit 1618: another installation is running" ;;
    5003) die "installer exit 5003: bootstrapper failed to download the installer" ;;
    5005) die "installer exit 5005: command-line parse error; the arguments above are wrong" ;;
    5007) die "installer exit 5007: operation blocked, the machine does not meet the requirements" ;;
    8005) die "installer exit 8005: verifying source payloads failed" ;;
    8006) die "installer exit 8006: Visual Studio processes are running" ;;
    8010) die "installer exit 8010: operating system not supported" ;;
    -1073720687) die "installer exit -1073720687: connectivity failure reaching Microsoft's servers" ;;
    -1073741510) die "installer exit -1073741510: the installer was terminated" ;;
    "") die "the installer wrote no exit code; it did not run to completion" ;;
    *) die "installer exit $rc: undocumented. Its own dd_*.log tails are printed above." ;;
  esac

  # Re-resolve the toolset: an --add can create a newer VC/Tools/MSVC directory,
  # and checking the old one would then report a failure that is not real.
  toolset="$(windows_msvc_toolset_root || true)"
  [ -n "$toolset" ] || die "no VC/Tools/MSVC/14.* toolset after installing; the install did not land"
  say "toolset after install: $(basename "$toolset")"

  still=()
  while IFS=' ' read -r component probe; do
    [ -n "$component" ] || continue
    [ -e "$toolset/$probe" ] || still+=("$component: $toolset/$probe")
  done < <(windows_vs_component_probes)
  if [ "${#still[@]}" -gt 0 ]; then
    printf 'error: the installer exited %s but these are still absent:\n' "${rc:-?}" >&2
    for entry in "${still[@]}"; do printf '  - %s\n' "$entry" >&2; done
    printf 'A 3010 means a reboot is needed before the install can be USED. If that is\n' >&2
    printf 'what happened, provisioning at job start cannot work for these components and\n' >&2
    printf 'they have to be baked into the runner image instead.\n' >&2
    exit 1
  fi
  say "every required Visual Studio component is present after installing"
  if [ -d "$toolset/atlmfc" ]; then
    printf '  atlmfc payload: %s MB\n' "$(du -sm "$toolset/atlmfc" | cut -f1)"
  fi
fi

# ---------------------------------------------------------------------------
# Phase 2: the Windows SDK's Debugging Tools feature
# ---------------------------------------------------------------------------
#
# The installer is pinned by URL AND by SHA-256, because a pinned URL only
# promises a name. build/WINDOWS_SDK_INSTALLER_URL is the version-specific
# 10.0.26100 link, not a "latest SDK" link -- the same link Chromium's own
# upstream neighbour (microsoft/WindowsAppSDK build/scripts/windows-sdk.ps1)
# uses for this SDK version. Only OptionId.WindowsDesktopDebuggers is requested,
# never Microsoft's "/features +".
#
# Which SDK version the build uses is NOT decided here and cannot drift:
# build/vs_toolchain.py hardcodes SDK_VERSION = '10.0.26100.0' and prints it
# verbatim as gn's sdk_version, with build/toolchain/win/setup_toolchain.py
# holding a second copy as a cross-check. There is no version autodetection to
# mislead, so an SDK directory appearing here can never be selected over the
# intended one. That pin travels with build/CHROMIUM_VERSION. This script still
# logs the version directories before and after, because a component of the
# pinned version DISAPPEARING is a real failure and the listing is how it would
# be recognised.

sdk_root="${WINDOWSSDKDIR:-/c/Program Files (x86)/Windows Kits/10}"
[ -d "$sdk_root" ] || die "no Windows SDK at $sdk_root"
dbghelp="$sdk_root/Debuggers/x64/dbghelp.dll"

list_versions() {
  for dir in bin Include Lib; do
    printf '  %s/%s: %s\n' "$(basename "$sdk_root")" "$dir" \
      "$(ls "$sdk_root/$dir" 2>/dev/null | tr '\n' ' ' || true)"
  done
}

if [ -f "$dbghelp" ]; then
  say "debugging tools already present at $sdk_root/Debuggers"
  list_versions
  exit 0
fi

url="$(pin WINDOWS_SDK_INSTALLER_URL)"
version="$(pin WINDOWS_SDK_INSTALLER_VERSION)"
want_sha="$(pin WINDOWS_SDK_INSTALLER_SHA256)"

say "SDK version directories before install"
list_versions

installer_exe="$work/winsdksetup.exe"

say "downloading pinned Windows SDK $version installer"
curl --fail --location --silent --show-error --retry 3 --output "$installer_exe" "$url" ||
  die "could not download the pinned SDK installer from $url"
[ -s "$installer_exe" ] || die "downloaded installer is empty"

got_sha="$(sha256sum "$installer_exe" | cut -d' ' -f1)"
say "installer sha256 $got_sha"
[ "$got_sha" = "$want_sha" ] ||
  die "installer digest does not match build/WINDOWS_SDK_INSTALLER_SHA256
  expected $want_sha
  got      $got_sha
The pinned URL served different bytes. Verify the release before repinning."

say "installing only OptionId.WindowsDesktopDebuggers"
# MSYS rewrites arguments that look like paths, so /features would arrive as a
# Windows path and the installer would reject it.
set +e
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  "$installer_exe" /features OptionId.WindowsDesktopDebuggers /quiet /norestart
sdk_rc=$?
set -e
# 3010 is ERROR_SUCCESS_REBOOT_REQUIRED: installed, reboot pending. The files
# are in place, and the dbghelp.dll check below is the real success signal.
case "$sdk_rc" in
  0) ;;
  3010) say "installer reported a pending reboot (3010); files are in place" ;;
  *) die "winsdksetup.exe exited $sdk_rc" ;;
esac

say "SDK version directories after install"
list_versions

[ -f "$dbghelp" ] || die "install reported success but $dbghelp is still missing"
say "dbghelp.dll present at $sdk_root/Debuggers/x64"
