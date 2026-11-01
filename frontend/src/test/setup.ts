import '@testing-library/jest-dom/vitest';

// jsdom lacks the layout/observer APIs the workbench touches; provide no-op
// implementations so component tests exercise the real components.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
window.scrollTo = () => {};
class FakeIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!('IntersectionObserver' in globalThis)) {
  (globalThis as Record<string, unknown>).IntersectionObserver = FakeIntersectionObserver;
}
