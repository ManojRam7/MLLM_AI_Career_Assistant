/** @type {import('next').NextConfig} */

// GitHub Pages serves at https://<user>.github.io/<repo>/ , so the app needs a base path.
// The Pages workflow sets PAGES_BASE_PATH=/MLLM_AI_Career_Assistant ; local dev leaves it empty.
const basePath = process.env.PAGES_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  output: "export",            // static site (out/) — no server needed, works on GitHub Pages
  trailingSlash: true,         // emit /index.html so Pages serves the route cleanly
  basePath,
  assetPrefix: basePath || undefined,
  images: { unoptimized: true },
};

export default nextConfig;
