# SAPR Mission Control

The Vite app reads its simulation data only from `public/data/`; no server-side API or remote runtime assets are required.

## Development

```powershell
cd web
npm install
npm run dev
```

## Static build

```powershell
cd web
npm run build
```

The production files are written to `dist/`.

## Earth texture

`public/textures/blue-marble.png` is NASA's Blue Marble Next Generation land/ocean image. The source URL and public-domain notice are in `public/textures/SOURCE.md`.
