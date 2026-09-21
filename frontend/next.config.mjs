/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Standalone output keeps the server self-contained for the Docker image.
  output: "standalone",
  // Dev server is also reached over the LAN (https://<lan-ip>/ via nginx)
  // for phone testing — otherwise cross-origin dev resources (HMR) 403.
  allowedDevOrigins: ["192.168.10.83", "localhost", "127.0.0.1"],
};

export default nextConfig;