import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import Chatbot from './Chatbot';

// Mock Lucide icons that might cause issues with simple rendering in JSDOM
vi.mock('lucide-react', async () => {
  const actual = await vi.importActual<typeof import('lucide-react')>('lucide-react');
  return {
    ...actual,
    // Add custom mocks if any throw errors
  };
});

// Mock fetch globally
global.fetch = vi.fn().mockImplementation((url) => {
  if (url.includes('last_update')) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ last_update: '2026-07-06T17:00:00Z' }),
    } as Response);
  }
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({}),
  } as Response);
});

describe('Chatbot Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the header and welcome message', async () => {
    render(<Chatbot />);
    
    // Check main header
    expect(screen.getByText('UTCI Heat Tracker')).toBeInTheDocument();
    
    // Check description contains welcome message
    expect(screen.getByText(/Hello! I am the UTCI Tracker Agent/)).toBeInTheDocument();
  });
});
