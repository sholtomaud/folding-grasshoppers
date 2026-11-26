# Shared settings for the release scripts. Source it; do not run it.
# Every value can be overridden from the environment.
#
# Placeholders still to fill in (SPEC.md 4): TEAM_ID and NOTARY_PROFILE.

APP_NAME="${APP_NAME:-CardboardCAD}"
BUNDLE_ID="${BUNDLE_ID:-com.example.cardboardcad}"

# Apple Developer Team ID (10 characters), e.g. ABCDE12345.
TEAM_ID="${TEAM_ID:-}"
# codesign identity: a certificate name or SHA-1. Empty + TEAM_ID set means
# "the Developer ID Application certificate for TEAM_ID in the keychain".
# "-" means ad-hoc (local/CI layout checks only; cannot be notarized).
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
# Keychain profile created with:
#   xcrun notarytool store-credentials "$NOTARY_PROFILE" --apple-id ... --team-id ...
NOTARY_PROFILE="${NOTARY_PROFILE:-cardboardcad-notary}"

# Where the Python runtime goes inside the .app (see scripts/README.md):
#   resources  -> Contents/Resources/python    (default: passes codesign --strict)
#   frameworks -> Contents/Frameworks/python   (SPEC first choice; codesign rejects it)
#   helpers    -> Contents/Helpers/python      (SPEC fallback)
PYTHON_LAYOUT="${PYTHON_LAYOUT:-resources}"
ARCHS="${ARCHS:-arm64}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"
APP_BUNDLE="${APP_BUNDLE:-$BUILD/app/$APP_NAME.app}"
DMG_PATH="${DMG_PATH:-$BUILD/$APP_NAME.dmg}"

python_home_rel() {
  case "$PYTHON_LAYOUT" in
    frameworks) echo "Contents/Frameworks/python" ;;
    helpers)    echo "Contents/Helpers/python" ;;
    resources)  echo "Contents/Resources/python" ;;
    *) echo "error: unknown PYTHON_LAYOUT=$PYTHON_LAYOUT" >&2; return 1 ;;
  esac
}

resolve_identity() {
  if [ -n "$SIGN_IDENTITY" ]; then return 0; fi
  [ -n "$TEAM_ID" ] || die "set SIGN_IDENTITY or TEAM_ID (scripts/config.sh)"
  local matches
  matches="$(security find-identity -v -p codesigning | grep "Developer ID Application" | grep "($TEAM_ID)" || true)"
  [ "$(printf '%s' "$matches" | grep -c .)" -eq 1 ] ||
    die "expected exactly one 'Developer ID Application ... ($TEAM_ID)' identity, found:
$matches"
  SIGN_IDENTITY="$(printf '%s' "$matches" | awk '{print $2}')"
}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
