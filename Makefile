.PHONY: build dev backend frontend clean test

# Build the frontend, copy the dist into the backend's static dir, then
# produce a wheel via uv. The wheel ships the SPA inside the package.
build:
	cd frontend && npm run build
	rm -rf backend/src/gravelord/static
	mkdir -p backend/src/gravelord/static
	cp -R frontend/dist/. backend/src/gravelord/static/
	cd backend && uv build

# Run the backend with reload + the Vite dev server in parallel. The Vite
# dev server proxies /api/* and /api/stream to the backend.
dev:
	@trap 'kill 0' EXIT; \
		(cd backend && uv run uvicorn gravelord.main:app --reload --host 127.0.0.1 --port 7777) & \
		(cd frontend && npm run dev) & \
		wait

backend:
	cd backend && uv run uvicorn gravelord.main:app --reload --host 127.0.0.1 --port 7777

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest

clean:
	rm -rf backend/dist backend/src/gravelord/static
	rm -rf frontend/dist frontend/node_modules
