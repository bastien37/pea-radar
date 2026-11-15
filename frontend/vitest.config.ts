// Configuration des tests, séparée de vite.config.ts : le serveur de développement
// n'a ainsi pas besoin que vitest soit installé.
import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config";

export default mergeConfig(viteConfig, defineConfig({
  test: { environment: "jsdom", restoreMocks: true },
}));
