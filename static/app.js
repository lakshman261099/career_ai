// Tab switching on the client side
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  const id = btn.dataset.tab;
  document.querySelectorAll(".tab-btn").forEach(b => {
    b.classList.toggle("bg-brand", b.dataset.tab === id);
    if (b.dataset.tab !== id) b.classList.remove("bg-brand");
  });
  document.querySelectorAll(".panel").forEach(p => p.classList.add("hidden"));
  const panel = document.getElementById(`panel-${id}`);
  if (panel) panel.classList.remove("hidden");
});
