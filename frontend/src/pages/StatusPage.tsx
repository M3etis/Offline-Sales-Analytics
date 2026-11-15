import ServicesStatus from '../components/ServicesStatus';
import { Page, PageHeader } from '../shared/ui';
import './StatusPage.css';

export default function StatusPage() {
  return (
    <Page width="narrow">
      <PageHeader title="Статус служб" description="Сервер приложения и Ollama, которые работают на этом компьютере. Выбор модели — в настройках ассистента" />
      <div className="status-page">
        <ServicesStatus />
      </div>
    </Page>
  );
}
