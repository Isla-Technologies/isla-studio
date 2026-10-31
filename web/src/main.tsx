import { render } from 'preact';
import './brand/isla-tokens.css';
import './styles/base.css';
import './styles/app.css';
import { App } from './app';

render(<App />, document.getElementById('app')!);
