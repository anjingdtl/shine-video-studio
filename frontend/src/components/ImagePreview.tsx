import { useEffect, useRef, useState } from 'react';

/**
 * A small derivative in the grid; original bytes load only on explicit preview.
 *
 * The server may refuse to build a derivative (no decoder, a corrupt file, a
 * file that is no longer on disk).  That is a stated state, not a broken image:
 * the card says so and still offers the original, so the user is never left
 * looking at an unexplained empty frame.
 */
export function ImagePreview({ thumbnail, original, label }: { thumbnail: string; original: string; label: string }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open) dialog.current?.showModal();
  }, [open]);
  return <>
    <button type="button" className="media-preview-button" aria-label={`放大查看 ${label}`} onClick={() => setOpen(true)}>
      {failed
        ? <span className="media-preview-unavailable">预览不可用</span>
        : <img src={thumbnail} alt={label} loading="lazy" decoding="async"
          onError={() => setFailed(true)} />}
    </button>
    {open && <dialog ref={dialog} className="media-preview-dialog" aria-label={`图片预览 ${label}`} onClose={() => setOpen(false)}>
      <button type="button" onClick={() => { dialog.current?.close(); setOpen(false); }}>关闭预览</button>
      <img src={original} alt={label} />
      <p>{label}</p>
    </dialog>}
  </>;
}
