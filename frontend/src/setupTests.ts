import '@testing-library/jest-dom';

// Mock scrollIntoView since JSDOM does not implement it
window.HTMLElement.prototype.scrollIntoView = function() {};
