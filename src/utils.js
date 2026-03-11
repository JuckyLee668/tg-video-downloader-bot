// utilities reused from the original channel downloader
import dotenv from 'dotenv';

dotenv.config();

export function parseProxy() {
  const proxyUrl = process.env.PROXY_URL;
  if (!proxyUrl) {
    return undefined;
  }

  try {
    const url = new URL(proxyUrl);
    const proxyType = url.protocol.replace(':', '').toLowerCase();

    if (proxyType === 'http' || proxyType === 'https') {
      return {
        ip: url.hostname,
        port: parseInt(url.port) || (proxyType === 'https' ? 443 : 80),
        username: url.username || undefined,
        password: url.password || undefined,
        type: 0, // HTTP proxy
      };
    } else if (proxyType === 'socks5') {
      return {
        ip: url.hostname,
        port: parseInt(url.port) || 1080,
        username: url.username || undefined,
        password: url.password || undefined,
        type: 2, // SOCKS5
      };
    } else if (proxyType === 'socks4') {
      return {
        ip: url.hostname,
        port: parseInt(url.port) || 1080,
        type: 1, // SOCKS4
      };
    } else {
      console.warn(`unsupported proxy type: ${proxyType}`);
      return undefined;
    }
  } catch (error) {
    console.warn(`failed to parse PROXY_URL: ${proxyUrl}. check format.`);
    return undefined;
  }
}

export function sanitizeFileName(fileName) {
  if (!fileName) return 'file';
  const illegalChars = /[<>:"|?*\\\/]/g;
  fileName = fileName.replace(illegalChars, '_');
  fileName = fileName.replace(/[\x00-\x1f\x80-\x9f]/g, '');
  fileName = fileName.trim().replace(/^\.+|\.+$/g, '');
  if (!fileName) fileName = 'file';
  if (fileName.length > 200) {
    const ext = fileName.substring(fileName.lastIndexOf('.'));
    const name = fileName.substring(0, 200 - ext.length);
    fileName = name + ext;
  }
  return fileName;
}