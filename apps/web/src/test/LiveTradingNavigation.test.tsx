import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { App } from '../main';

vi.mock('../LiveCryptoTape', () => ({ default: () => null }));
vi.mock('../MarketMechanics', () => ({ default: () => null }));
vi.mock('../PaperPipelinePanel', () => ({ PaperPipelinePanel: () => null }));
vi.mock('../IntelligencePanel', () => ({ IntelligencePanel: () => null }));

beforeEach(() => localStorage.clear());

it.each(['Live Trading', 'Settings'])('opens the setup form from %s', page => {
  render(<App />);
  fireEvent.click(screen.getByRole('button', { name: new RegExp(page) }));
  expect(screen.getByRole('region', { name: 'Live trading setup' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Activate live trading' })).toBeDisabled();
  expect(screen.queryByText('AI Trading Signals')).not.toBeInTheDocument();
});
