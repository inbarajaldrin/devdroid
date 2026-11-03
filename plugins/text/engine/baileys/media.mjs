import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
function videoToGif(src) {
  const out = path.join(os.tmpdir(), `wa_v2g_${Date.now()}.gif`);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', src,
    '-vf', 'fps=15,scale=512:-1:flags=lanczos', '-t', '6', out]);
  return out;
}
async function toWebpSticker(src, adapters = {}) {
  const sharp = adapters.sharp || (await import('sharp')).default;
  if (src.toLowerCase().endsWith('.webp')) return { out: src, animated: (await sharp(src).metadata()).pages > 1 };
  let input = src;
  if (['.mp4', '.mov', '.webm', '.m4v'].includes(path.extname(src).toLowerCase())) input = videoToGif(src);
  const animated = path.extname(input).toLowerCase() === '.gif';
  const out = path.join(os.tmpdir(), `wa_sticker_${randomUUID()}.webp`);
  await sharp(input, { animated })
    .resize(512, 512, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .webp({ quality: 70, loop: 0 })
    .toFile(out);
  return { out, animated };
}
function gifToMp4(src) {
  const out = path.join(os.tmpdir(), `wa_gif_${Date.now()}.mp4`);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', src, '-movflags', '+faststart',
    '-pix_fmt', 'yuv420p', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', out]);
  return out;
}
const MIME = {
  '.pdf': 'application/pdf', '.zip': 'application/zip', '.txt': 'text/plain', '.md': 'text/markdown',
  '.csv': 'text/csv', '.json': 'application/json', '.html': 'text/html', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.mp4': 'video/mp4', '.mov': 'video/quicktime',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};


export { toWebpSticker, gifToMp4, MIME };
