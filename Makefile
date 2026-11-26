# CardboardCAD developer entry points.
#
# Python, uv and Node never run on the Mac itself: those targets run in the
# dev container (Containerfile) through Apple's `container` CLI, with this
# repo mounted at /work. Xcode, Swift and codesign targets run on the Mac.
#
#   make help
#
# Run `container system start` once after installing Apple's container CLI.

IMAGE ?= cardboardcad-dev
export CARDBOARD_IMAGE := $(IMAGE)
TOOLS := scripts/tools.sh
APP   := build/app/CardboardCAD.app

.DEFAULT_GOAL := help
.PHONY: help image shell test lock runtime smoke swift-test verify verify-fast \
        app sign-adhoc install open release clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  make %-12s %s\n", $$1, $$2}'

# ---- in the dev container ------------------------------------------------

image: ## Build (or rebuild) the dev container image
	container build --tag $(IMAGE) --file Containerfile .

shell: ## Open a shell in the dev container
	container run --rm --interactive --tty --volume "$(CURDIR):/work" --workdir /work $(IMAGE) bash

test: ## Engine tests (pytest) [container]
	$(TOOLS) bash -c 'cd engine && uv run --frozen pytest -q'

lock: ## Re-lock engine deps: uv.lock + requirements.lock [container]
	$(TOOLS) bash -c 'cd engine && uv lock && uv export --frozen --format requirements-txt --no-dev --no-emit-project -o requirements.lock'

runtime: ## Build build/python-runtime for the app (cross-built) [container], then smoke-test it [Mac]
	$(TOOLS) scripts/build_python_runtime.sh
	scripts/smoke_runtime.sh

# ---- on the Mac ----------------------------------------------------------

smoke: ## Smoke-test build/python-runtime with its own interpreter [Mac]
	scripts/smoke_runtime.sh

swift-test: ## CardboardCADKit Swift tests [Mac]
	cd app/CardboardCADKit && swift test --parallel

verify: ## Full gate: pytest + proof seal [container], Swift tests [Mac]
	scripts/verify.sh

verify-fast: ## Quick gate: pytest + proof seal [container]
	scripts/verify.sh --fast

app: runtime ## Build the unsigned app into build/app [Mac]
	scripts/build_app.sh

sign-adhoc: ## Ad-hoc sign build/app so it opens on this Mac [Mac]
	SIGN_IDENTITY=- scripts/sign.sh $(APP)

install: ## Copy build/app/CardboardCAD.app to /Applications [Mac]
	rm -rf /Applications/CardboardCAD.app
	ditto $(APP) /Applications/CardboardCAD.app
	@echo "Installed /Applications/CardboardCAD.app"

open: ## Open the installed app
	open /Applications/CardboardCAD.app

release: ## Signed + notarized DMG; needs TEAM_ID and NOTARY_PROFILE [Mac + container]
	scripts/release.sh

clean: ## Delete build/ (keeps nothing, including the download cache)
	rm -rf build
