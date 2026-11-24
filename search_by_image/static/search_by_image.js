// Search by image: choosing, dropping or pasting a picture searches right away.
// Large photos are scaled down in the browser first, so phones upload a few
// hundred kilobytes instead of several megabytes. Redrawing the picture also
// turns it upright and drops its metadata, and converts formats the server
// doesn't read (HEIC, AVIF) wherever the browser can display them. The server
// removes metadata again in any case.
(() => {
  "use strict";

  const form = document.getElementById("sbi-form");
  if (!form) return;
  const fileInput = document.getElementById("sbi-file");
  const urlInput = document.getElementById("sbi-url");
  const drop = document.getElementById("sbi-drop");
  const busy = document.getElementById("sbi-busy");
  const anime = form.querySelector('input[name="anime"]');

  const MAX_EDGE = 1600;
  const SEND_AS_IS = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
  const ANIME_KEY = "search_by_image.anime";

  // Remember the anime box between visits; a submitted form keeps what was sent.
  if (anime) {
    if (!form.dataset.submitted && localStorage.getItem(ANIME_KEY) !== null) {
      anime.checked = localStorage.getItem(ANIME_KEY) === "1";
    }
    anime.addEventListener("change", () => localStorage.setItem(ANIME_KEY, anime.checked ? "1" : "0"));
  }

  const prepare = async (file) => {
    let bitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      return file; // the browser can't read it either: let the server decide
    }
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && SEND_AS_IS.has(file.type) && file.size <= 4 * 1024 * 1024) {
      bitmap.close();
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // JPEG has no transparency
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
    return blob ? new File([blob], "image.jpg", { type: "image/jpeg" }) : file;
  };

  const showBusy = () => {
    busy.hidden = false;
    form.classList.add("sbi-busy");
  };

  const searchFile = async (file) => {
    if (!file) return;
    showBusy();
    const ready = await prepare(file);
    const files = new DataTransfer();
    files.items.add(ready);
    fileInput.files = files.files;
    urlInput.value = "";
    form.submit();
  };

  const searchUrl = (url) => {
    urlInput.value = url;
    fileInput.value = "";
    showBusy();
    form.submit();
  };

  const isLink = (text) => /^https?:\/\/\S+$/i.test(text.trim());

  fileInput.addEventListener("change", () => searchFile(fileInput.files[0]));

  form.addEventListener("submit", () => {
    // A typed link wins over an earlier file choice.
    if (urlInput.value.trim()) fileInput.value = "";
    showBusy();
  });

  document.addEventListener("paste", (event) => {
    const data = event.clipboardData;
    if (!data) return;
    const item = [...data.items].find((entry) => entry.kind === "file" && entry.type.startsWith("image/"));
    if (item) {
      event.preventDefault();
      searchFile(item.getAsFile());
      return;
    }
    const text = data.getData("text/plain");
    if (document.activeElement !== urlInput && isLink(text)) {
      event.preventDefault();
      searchUrl(text.trim());
    }
  });

  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("sbi-dragging");
    }),
  );
  ["dragleave", "dragend"].forEach((name) => drop.addEventListener(name, () => drop.classList.remove("sbi-dragging")));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("sbi-dragging");
    const file = event.dataTransfer.files[0];
    if (file) {
      searchFile(file);
      return;
    }
    // A picture dragged from another web page arrives as its link.
    const link = (event.dataTransfer.getData("text/uri-list") || "").split("\n").find((line) => isLink(line));
    if (link) searchUrl(link.trim());
  });
})();
