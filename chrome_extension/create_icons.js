// icons.js - 生成图标
// 在扩展目录下运行: node icons.js

const fs = require('fs');

function createIcon(size, color) {
    const canvas = require('canvas').createCanvas(size, size);
    const ctx = canvas.getContext('2d');
    
    // 背景
    const gradient = ctx.createLinearGradient(0, 0, size, size);
    gradient.addColorStop(0, '#1a1a2e');
    gradient.addColorStop(1, '#16213e');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
    
    // T字标识
    ctx.fillStyle = color;
    ctx.font = `bold ${size * 0.6}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('T', size / 2, size / 2);
    
    // 边框
    ctx.strokeStyle = color;
    ctx.lineWidth = size * 0.05;
    ctx.strokeRect(size * 0.1, size * 0.1, size * 0.8, size * 0.8);
    
    return canvas.toBuffer('image/png');
}

// 如果没有canvas模块，创建简单的SVG图标
function createSVGIcon(size) {
    const svg = `
<svg width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1a1a2e"/>
      <stop offset="100%" style="stop-color:#16213e"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect x="10%" y="10%" width="80%" height="80%" fill="none" stroke="#e94560" stroke-width="${size * 0.05}"/>
  <text x="50%" y="55%" font-family="Arial" font-size="${size * 0.6}" font-weight="bold" 
        fill="#e94560" text-anchor="middle" dominant-baseline="middle">T</text>
</svg>`;
    return Buffer.from(svg);
}

// 创建图标
const sizes = [16, 48, 128];

sizes.forEach(size => {
    const svg = createSVGIcon(size);
    fs.writeFileSync(`icon${size}.png.svg`, svg);
    console.log(`Created icon${size}.png.svg (${size}x${size})`);
});

console.log('\n请使用图像处理工具将SVG转换为PNG，或使用以下简单方法:');
console.log('1. 打开 icon128.png.svg 在浏览器中');
console.log('2. 截图并保存为 icon128.png');
console.log('3. 缩小为 48x48 和 16x16 版本');
