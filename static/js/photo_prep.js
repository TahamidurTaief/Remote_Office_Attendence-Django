/**
 * FieldTrack Shared Browser Photo-Preparation Helper (photo_prep.js)
 *
 * Responsibilities:
 * 1. Preserves image orientation (EXIF) and aspect ratio.
 * 2. Never upscales: if longest edge <= 1280px, original dimensions are preserved.
 * 3. Resizes longest edge to 1280px if larger.
 * 4. Encodes to JPEG at quality 0.8.
 * 5. Single-pass processing: produces a prepared Blob once after capture,
 *    reused for live preview, form submission, offline storage, and retries.
 * 6. Preserves punch UUID and capture timestamp.
 * 7. Graceful fallback: if canvas or browser conversion fails, retains original
 *    valid photo and falls back to server validation; never silently discards.
 */

(function (window) {
  'use strict';

  const DEFAULT_MAX_DIMENSION = 1280;
  const DEFAULT_QUALITY = 0.8;
  const DEFAULT_MIME = 'image/jpeg';

  /**
   * Calculates scaled dimensions preserving aspect ratio without upscaling.
   *
   * @param {number} width - Original image width
   * @param {number} height - Original image height
   * @param {number} maxDimension - Max allowable length for longest edge (default: 1280)
   * @returns {{ width: number, height: number, scaled: boolean }}
   */
  function calculateDimensions(width, height, maxDimension = DEFAULT_MAX_DIMENSION) {
    if (!width || !height) return { width: 0, height: 0, scaled: false };
    const longest = Math.max(width, height);
    if (longest <= maxDimension) {
      return { width, height, scaled: false };
    }
    const scale = maxDimension / longest;
    return {
      width: Math.round(width * scale),
      height: Math.round(height * scale),
      scaled: true
    };
  }

  /**
   * Converts a Blob to a Base64 Data URL.
   *
   * @param {Blob} blob
   * @returns {Promise<string>}
   */
  function blobToDataURL(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = (e) => reject(e);
      reader.readAsDataURL(blob);
    });
  }

  /**
   * Prepares a captured photo:
   * - Decodes image respecting EXIF orientation.
   * - Scales down longest edge to 1280px (never upscales).
   * - Encodes to image/jpeg at quality 0.8.
   * - Falls back to original File/Blob if canvas conversion fails.
   *
   * @param {File|Blob} fileOrBlob - Input file from camera/file input
   * @param {Object} [options]
   * @param {number} [options.maxDimension=1280]
   * @param {number} [options.quality=0.8]
   * @param {string} [options.mimeType='image/jpeg']
   * @param {string} [options.captureTimestamp]
   * @returns {Promise<Object>}
   */
  async function preparePhoto(fileOrBlob, options = {}) {
    if (!fileOrBlob || !(fileOrBlob instanceof Blob)) {
      return null;
    }

    const maxDimension = options.maxDimension || DEFAULT_MAX_DIMENSION;
    const quality = typeof options.quality === 'number' ? options.quality : DEFAULT_QUALITY;
    const mimeType = options.mimeType || DEFAULT_MIME;
    const captureTimestamp = options.captureTimestamp || new Date().toISOString();

    const originalName = fileOrBlob.name || 'attendance_photo.jpg';
    const baseName = originalName.substring(0, originalName.lastIndexOf('.')) || originalName;
    const preparedFilename = `${baseName}.jpg`;

    let sourceBitmap = null;
    let imgElement = null;
    let objectUrl = null;

    try {
      let sourceWidth = 0;
      let sourceHeight = 0;

      // 1. Decode with EXIF orientation preservation via createImageBitmap if supported
      if (typeof window.createImageBitmap === 'function') {
        try {
          sourceBitmap = await window.createImageBitmap(fileOrBlob, {
            imageOrientation: 'from-image'
          });
          sourceWidth = sourceBitmap.width;
          sourceHeight = sourceBitmap.height;
        } catch (bitmapErr) {
          sourceBitmap = null;
        }
      }

      // Fallback decode via HTMLImageElement (modern browsers support CSS image-orientation: from-image)
      if (!sourceBitmap) {
        objectUrl = URL.createObjectURL(fileOrBlob);
        imgElement = await new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = (e) => reject(new Error('Failed to decode image element'));
          img.src = objectUrl;
        });
        sourceWidth = imgElement.naturalWidth || imgElement.width;
        sourceHeight = imgElement.naturalHeight || imgElement.height;
      }

      if (!sourceWidth || !sourceHeight) {
        throw new Error('Invalid image dimensions');
      }

      // 2. Compute proportional dimensions without upscaling
      const { width: targetWidth, height: targetHeight } = calculateDimensions(
        sourceWidth,
        sourceHeight,
        maxDimension
      );

      // 3. Render into Canvas
      const canvas = document.createElement('canvas');
      canvas.width = targetWidth;
      canvas.height = targetHeight;
      const ctx = canvas.getContext('2d', { alpha: false });
      if (!ctx) {
        throw new Error('Canvas 2D context unavailable');
      }

      if (sourceBitmap) {
        ctx.drawImage(sourceBitmap, 0, 0, targetWidth, targetHeight);
        if (typeof sourceBitmap.close === 'function') {
          sourceBitmap.close();
        }
      } else if (imgElement) {
        ctx.drawImage(imgElement, 0, 0, targetWidth, targetHeight);
      }

      // 4. Compress to JPEG 0.8
      const preparedBlob = await new Promise((resolve, reject) => {
        canvas.toBlob(
          (blob) => {
            if (blob && blob.size > 0) resolve(blob);
            else reject(new Error('Canvas toBlob generated invalid blob'));
          },
          mimeType,
          quality
        );
      });

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }

      const previewUrl = URL.createObjectURL(preparedBlob);
      const preparedFile = new File([preparedBlob], preparedFilename, { type: mimeType });

      let cachedDataUrl = null;

      return {
        blob: preparedBlob,
        file: preparedFile,
        previewUrl: previewUrl,
        width: targetWidth,
        height: targetHeight,
        originalWidth: sourceWidth,
        originalHeight: sourceHeight,
        originalSize: fileOrBlob.size,
        preparedSize: preparedBlob.size,
        mimeType: mimeType,
        prepared: true,
        fallback: false,
        captureTimestamp: captureTimestamp,
        getDataUrl: async function () {
          if (!cachedDataUrl) {
            cachedDataUrl = await blobToDataURL(preparedBlob);
          }
          return cachedDataUrl;
        },
        destroy: function () {
          if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
          }
        }
      };
    } catch (err) {
      console.warn('[FieldTrackPhoto] Browser conversion failed, retaining original photo:', err);

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }

      const previewUrl = URL.createObjectURL(fileOrBlob);
      let cachedDataUrl = null;

      return {
        blob: fileOrBlob,
        file: fileOrBlob,
        previewUrl: previewUrl,
        width: null,
        height: null,
        originalSize: fileOrBlob.size,
        preparedSize: fileOrBlob.size,
        mimeType: fileOrBlob.type || 'image/jpeg',
        prepared: false,
        fallback: true,
        error: err ? (err.message || String(err)) : 'Unknown error',
        captureTimestamp: captureTimestamp,
        getDataUrl: async function () {
          if (!cachedDataUrl) {
            cachedDataUrl = await blobToDataURL(fileOrBlob);
          }
          return cachedDataUrl;
        },
        destroy: function () {
          if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
          }
        }
      };
    }
  }

  const FieldTrackPhoto = {
    DEFAULT_MAX_DIMENSION,
    DEFAULT_QUALITY,
    DEFAULT_MIME,
    calculateDimensions,
    preparePhoto,
    blobToDataURL
  };

  window.FieldTrackPhoto = FieldTrackPhoto;
  window.PhotoPrep = FieldTrackPhoto; // Convenience alias
})(window);
