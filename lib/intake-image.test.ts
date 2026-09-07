import assert from 'node:assert/strict';
import test from 'node:test';
import {
  IMAGE_MAX_BYTES,
  IMAGE_TYPES,
  describeImage,
  imageSignature,
  validateImage,
} from './intake-image';

void test('the accepted types and size cap match the intake endpoint', () => {
  assert.deepEqual([...IMAGE_TYPES], ['image/png', 'image/jpeg']);
  assert.equal(IMAGE_MAX_BYTES, 5 * 1024 * 1024);
});

void test('validateImage accepts a PNG or JPEG within the cap', () => {
  assert.equal(
    validateImage({ type: 'image/png', size: 1024, name: 'shot.png' }),
    null,
  );
  assert.equal(
    validateImage({ type: 'image/jpeg', size: IMAGE_MAX_BYTES, name: 'a.jpg' }),
    null,
  );
});

void test('validateImage rejects anything that is not a PNG or JPEG', () => {
  const message = 'Choose a PNG or JPEG image.';
  assert.equal(
    validateImage({ type: 'application/pdf', size: 10, name: 'notes.pdf' }),
    message,
  );
  assert.equal(
    validateImage({ type: 'image/gif', size: 10, name: 'loop.gif' }),
    message,
  );
  assert.equal(
    validateImage({ type: '', size: 10, name: 'shot.png' }),
    message,
  );
});

void test('validateImage rejects an image over five megabytes', () => {
  assert.equal(
    validateImage({
      type: 'image/png',
      size: IMAGE_MAX_BYTES + 1,
      name: 'big.png',
    }),
    'Images must be 5 MB or smaller.',
  );
});

void test('validateImage rejects an empty image', () => {
  assert.equal(
    validateImage({ type: 'image/jpeg', size: 0, name: 'empty.jpg' }),
    'That image is empty.',
  );
});

void test('validateImage reports the wrong type before the size', () => {
  assert.equal(
    validateImage({ type: 'text/plain', size: 0, name: 'empty.txt' }),
    'Choose a PNG or JPEG image.',
  );
});

void test('imageSignature joins the identifying fields with pipes', () => {
  assert.equal(
    imageSignature({
      name: 'screenshot.png',
      size: 4096,
      lastModified: 1717171717000,
      type: 'image/png',
    }),
    'screenshot.png|4096|1717171717000|image/png',
  );
});

void test('imageSignature separates two files that differ in one field', () => {
  const base = {
    name: 'a.png',
    size: 10,
    lastModified: 5,
    type: 'image/png',
  };
  assert.notEqual(imageSignature(base), imageSignature({ ...base, size: 11 }));
  assert.notEqual(
    imageSignature(base),
    imageSignature({ ...base, lastModified: 6 }),
  );
  assert.equal(imageSignature(base), imageSignature({ ...base }));
});

void test('describeImage prints kilobytes below one megabyte', () => {
  assert.equal(
    describeImage({ size: 412 * 1024, type: 'image/png' }),
    '412 KB · PNG',
  );
  assert.equal(
    describeImage({ size: 1536, type: 'image/jpeg' }),
    '1.5 KB · JPEG',
  );
  assert.equal(describeImage({ size: 0, type: 'image/png' }), '0 KB · PNG');
});

void test('describeImage switches to megabytes at one megabyte', () => {
  assert.equal(
    describeImage({ size: 1024 * 1024, type: 'image/png' }),
    '1 MB · PNG',
  );
  assert.equal(
    describeImage({ size: 2.5 * 1024 * 1024, type: 'image/jpeg' }),
    '2.5 MB · JPEG',
  );
});

void test('describeImage falls back when the type is unusable', () => {
  assert.equal(describeImage({ size: 2048, type: '' }), '2 KB · IMAGE');
});
