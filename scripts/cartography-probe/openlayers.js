import Map from 'ol/Map.js';
import View from 'ol/View.js';
import Projection from 'ol/proj/Projection.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import ImageLayer from 'ol/layer/Image.js';
import ImageStatic from 'ol/source/ImageStatic.js';
import { Fill, Stroke, Style } from 'ol/style.js';

const scene = await (await fetch('/fixture.json')).json();
const font = new FontFace('ProbeLocalFont', 'url(/font.ttf)');
await font.load();
document.fonts.add(font);
await document.fonts.ready;
const px = (mm) => mm / 25.4 * scene.dpi;
const [left, bottom, frameWidth, frameHeight] = scene.frameMm;
const size = [Math.round(px(frameWidth)), Math.round(px(frameHeight))];
const target = document.getElementById('map');
target.style.width = `${size[0]}px`;
target.style.height = `${size[1]}px`;
const projection = new Projection({ code: scene.crs, units: 'm', extent: scene.extent });
const features = new GeoJSON().readFeatures(scene.collection, { dataProjection: projection, featureProjection: projection });
if (features.length !== 2103 || features.length > scene.budget.maxFeatures) throw new Error('Full-data count or budget mismatch');
const styles = Object.fromEntries(Object.entries(scene.colors).map(([name, hex]) => {
  const rgb = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
  return [name, new Style({ fill: new Fill({ color: [...rgb, scene.opacity] }),
    stroke: new Stroke({ color: [...rgb, scene.opacity], width: 0.3 / 72 * scene.dpi }) })];
}));
const source = new ImageStatic({ url: '/raster.png', projection, imageExtent: scene.extent, interpolate: false });
const map = new Map({ target, pixelRatio: 1, controls: [], interactions: [],
  layers: [new ImageLayer({ source }), new VectorLayer({ source: new VectorSource({ features, wrapX: false }),
    style: (feature) => styles[feature.get('category')] })],
  view: new View({ projection, center: [(scene.extent[0] + scene.extent[2]) / 2, (scene.extent[1] + scene.extent[3]) / 2],
    resolution: (scene.extent[2] - scene.extent[0]) / size[0], enableRotation: false, multiWorld: false }) });
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('OpenLayers render deadline exceeded')), 30000);
  source.once('imageloaderror', () => { clearTimeout(timer); reject(new Error('Raster did not load')); });
  map.once('rendercomplete', () => { clearTimeout(timer); resolve(); });
  map.renderSync();
});
const page = document.createElement('canvas');
[page.width, page.height] = scene.pixels;
if (page.width * page.height > scene.budget.maxOutputPixels) throw new Error('Output pixel budget exceeded');
const context = page.getContext('2d');
context.fillStyle = 'white';
context.fillRect(0, 0, page.width, page.height);
const offset = [px(left), px(scene.paperMm[1] - bottom - frameHeight)];
context.save();
context.translate(...offset);
for (const canvas of target.querySelectorAll('.ol-layer canvas')) {
  if (!canvas.width || !canvas.height) continue;
  context.save();
  context.globalAlpha = Number(canvas.parentNode.style.opacity || 1);
  const matrix = new DOMMatrix(canvas.style.transform);
  context.transform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
  context.drawImage(canvas, 0, 0);
  context.restore();
}
context.restore();
const coordinatePixel = (coordinate) => {
  const pixel = map.getPixelFromCoordinate(coordinate);
  return [offset[0] + pixel[0], offset[1] + pixel[1]];
};
context.fillStyle = '#202020';
context.font = `${16 / 72 * scene.dpi}px ProbeLocalFont`;
context.textAlign = 'center';
context.textBaseline = 'middle';
context.fillText(scene.title, page.width / 2, page.height * 0.045);
context.font = `${9 / 72 * scene.dpi}px ProbeLocalFont`;
context.textBaseline = 'bottom';
for (const label of scene.labels) context.fillText(label.text, ...coordinatePixel(label.coordinate));
context.font = `${8 / 72 * scene.dpi}px ProbeLocalFont`;
context.textAlign = 'left';
context.textBaseline = 'alphabetic';
context.fillText(scene.footer, px(left), px(scene.paperMm[1] - 10));
context.beginPath();
context.moveTo(...coordinatePixel([500100, 3000870]));
context.lineTo(...coordinatePixel([501100, 3000870]));
context.lineWidth = scene.dpi / 72;
context.strokeStyle = '#202020';
context.stroke();
context.textAlign = 'center';
context.fillText('1,000 m (grid)', ...coordinatePixel([500600, 3000900]));
const pixel = (coordinate) => [...context.getImageData(...coordinatePixel(coordinate).map(Math.round), 1, 1).data];
const blend = (category, background) => [1, 3, 5].map((index, channel) =>
  parseInt(scene.colors[category].slice(index, index + 2), 16) * scene.opacity + background[channel] * (1 - scene.opacity));
const base = [220, 230, 210], other = [180, 210, 230];
const points = [
  ['hole', [500450, 3001200], base], ['hole-shell', [500200, 3001200], blend('hole', base)],
  ['multipart-first', [501100, 3001200], blend('multipart', base)],
  ['multipart-second', [501500, 3001300], blend('multipart', other)],
  ['feature-2103', [502075, 3001300], blend('sentinel', other)],
  ['NoData', [500900, 3001600], [255, 255, 255]], ['grid', [500063, 3000059], blend('grid', base)],
];
const checks = points.map(([name, coordinate, expected]) => {
  const actual = pixel(coordinate);
  const maxChannelError = Math.max(...expected.map((value, channel) => Math.abs(value - actual[channel])));
  if (maxChannelError > 3 || actual[3] !== 255) throw new Error(`${name} failed: ${actual}, expected ${expected}`);
  return { name, actual, maxChannelError };
});
const control = coordinatePixel([501100, 3000870])[0] - coordinatePixel([500100, 3000870])[0];
if (Math.abs(control - px(100)) > 1) throw new Error('Projected physical control differs');
const full = page.toDataURL('image/png');
const preview = document.getElementById('preview');
preview.src = full;
await preview.decode();
window.probe = { ok: true, featureCount: features.length, renderedFeatureIds: features.map((feature) => feature.getId()),
  checks, pixels: [page.width, page.height], mapPixels: size, fontLoaded: font.status === 'loaded',
  physicalControlPixels: control,
  dataCrs: scene.crs, outputKind: 'raster PNG', pngDpiMetadata: 'browser default; requested paper size recorded in manifest',
  previewComplete: preview.complete, dataUrl: full };
map.dispose();
