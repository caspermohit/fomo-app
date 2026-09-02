import type { NextConfig } from "next";

const botOrigin = process.env.BOT_API_ORIGIN ?? "http://127.0.0.1:5055";
const uiHost = (() => {
  const origin = process.env.UI_ORIGIN;
  if (!origin) return null;
  try {
    return new URL(origin).hostname;
  } catch {
    return null;
  }
})();

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost", ...(uiHost ? [uiHost] : [])],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${botOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
