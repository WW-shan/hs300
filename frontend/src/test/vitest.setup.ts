type VitestJSDOMGlobal = typeof globalThis & {
  jsdom?: { window?: Window }
}

const jsdomWindow = (globalThis as VitestJSDOMGlobal).jsdom?.window

if (jsdomWindow) {
  // Node 26 defines storage globals that Vitest 3 does not replace with jsdom's.
  for (const storageName of ['localStorage', 'sessionStorage'] as const) {
    Object.defineProperty(globalThis, storageName, {
      configurable: true,
      value: jsdomWindow[storageName],
    })
  }
}
