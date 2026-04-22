import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useLocation } from 'react-router-dom';

import { renderWithProviders } from '../helpers/render';

function LocationProbe() {
  const location = useLocation();
  return <div>{location.pathname}</div>;
}

describe('renderWithProviders', () => {
  it('renders components with router context', () => {
    renderWithProviders(<LocationProbe />, { route: '/login' });

    expect(screen.getByText('/login')).toBeInTheDocument();
  });
});
