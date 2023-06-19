from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask import Flask, render_template
import matplotlib.pyplot as plt
import io
import base64
import pandas as pd
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'secret_key'  # Klucz do sesji

db = SQLAlchemy(app)

# Definicja modelu użytkownika
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(50))


with app.app_context():
    # Sprawdzenie, czy użytkownik już istnieje
    existing_user = User.query.filter_by(username='admin').first()

    if existing_user is None:
        # Dodanie użytkownika do bazy danych
        new_user = User(username='admin', password='admin')
        db.session.add(new_user)
        db.session.commit()

#https://stat.gov.pl/obszary-tematyczne/rynek-pracy/pracujacy-zatrudnieni-wynagrodzenia-koszty-pracy/wyrownania-sezonowe-przecietne-zatrudnienie-i-przecietne-miesieczne-wynagrodzenie,20,7.html
# Strona logowania
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()

        if user:
            session['username'] = username
            return redirect(url_for('analysis'))
        else:
            return render_template('login.html', error='Invalid username or password')

    return render_template('login.html')

# Strona analizy (dostępna po zalogowaniu)
@app.route('/analysis')
def analysis():
    df = pd.read_csv('2._przecietne_miesieczne_wynagrodzenia_brutto_w_sektorze_przedsiebiorstw_-_dane_miesieczne.csv', sep=";")

    # Konwersja kolumny 'Wartość' na typ float
    df['Wartość'] = df['Wartość'].str.replace(',', '.').astype(float)

    # Wyliczenie średniej wartości po roku
    df_avg = df.groupby('Rok')['Wartość'].mean().reset_index()

    # Tworzenie pierwszego wykresu liniowego
    fig1, ax1 = plt.subplots()
    ax1.plot(df_avg['Rok'], df_avg['Wartość'])
    ax1.set_xlabel('Rok')
    ax1.set_ylabel('Średnie wynagrodzenie brutto (zł)')


    # Zapis pierwszego wykresu do pamięci
    img1 = io.BytesIO()
    plt.savefig(img1, format='png')
    img1.seek(0)

    # Kodowanie obrazu pierwszego wykresu w formacie base64
    plot_url1 = base64.b64encode(img1.getvalue()).decode()

    # Generowanie drugiego wykresu
    current_year = df[df['Rok'] == df['Rok'].max()]
    previous_year = df[df['Rok'] == df['Rok'].max() - 1]

    common_months = set(current_year['Miesiąc']).intersection(set(previous_year['Miesiąc']))

    current_year_avg = current_year[current_year['Miesiąc'].isin(common_months)].groupby('Miesiąc')[
        'Wartość'].mean().reset_index()
    previous_year_avg = previous_year[previous_year['Miesiąc'].isin(common_months)].groupby('Miesiąc')[
        'Wartość'].mean().reset_index()

    growth = (current_year_avg['Wartość'] - previous_year_avg['Wartość']) / previous_year_avg['Wartość'] * 100

    # Tworzenie drugiego wykresu słupkowego
    fig2, ax2 = plt.subplots()
    ax2.bar(current_year_avg['Miesiąc'], current_year_avg['Wartość'], label='Obecny rok')
    ax2.bar(previous_year_avg['Miesiąc'], previous_year_avg['Wartość'], label='Poprzedni rok')

    # Dodawanie wartości wzrostu przy słupkach
    for i in range(len(current_year_avg)):
        ax2.text(current_year_avg['Miesiąc'][i], current_year_avg['Wartość'][i], f"{growth[i]:.2f}%", ha='center')

    ax2.set_xlabel('Miesiąc')
    ax2.set_ylabel('Średnie wynagrodzenie brutto (zł)')
    ax2.legend(loc='lower right')

    # Aktualizacja etykiet osi x
    plt.xticks(range(1, len(common_months) + 1), common_months)

    # Przesunięcie legendy poza wykres


    # Zapis drugiego wykresu do pamięci
    img2 = io.BytesIO()
    plt.savefig(img2, format='png')
    img2.seek(0)

    # Kodowanie obrazu drugiego wykresu w formacie base64
    plot_url2 = base64.b64encode(img2.getvalue()).decode()
    df3 = pd.read_csv('2._przecietne_miesieczne_wynagrodzenia_brutto_w_sektorze_przedsiebiorstw_-_dane_miesieczne.csv',
                      sep=";")

    df3['Wartość'] = df3['Wartość'].str.replace(',', '.').astype(float)

    # Obliczenie mediany wynagrodzeń dla każdego roku
    median_wynagrodzen = df3.groupby('Rok')['Wartość'].median().reset_index()

    # Tworzenie wykresu słupkowego
    fig3, ax3 = plt.subplots()
    ax3.bar(median_wynagrodzen['Rok'], median_wynagrodzen['Wartość'])
    ax3.set_xlabel('Rok')
    ax3.set_ylabel('Mediana wynagrodzeń brutto (zł)')

    # Zapis wykresu do obiektu BytesIO
    img3 = io.BytesIO()
    plt.savefig(img3, format='png')
    img3.seek(0)

    # Kodowanie obrazu w formacie base64
    plot_url3 = base64.b64encode(img3.getvalue()).decode()
    df_wynagrodzenia = pd.read_csv(
        '2._przecietne_miesieczne_wynagrodzenia_brutto_w_sektorze_przedsiebiorstw_-_dane_miesieczne.csv', sep=";")
    df_wynagrodzenia['Wartość'] = df_wynagrodzenia['Wartość'].str.replace(',', '.').astype(float)

    df_inflacja = pd.read_csv('roczne_wskazniki_cen_towarow_i_uslug_konsumpcyjnych_od_1950_roku_2.csv', sep=";")
    df_inflacja['Wartość_i'] = df_inflacja['Wartość_i'].str.replace(',', '.').astype(float)
    wskaźnik_wartości_realnej = df_inflacja.groupby('Rok')['Wartość_i'].mean().reset_index()

    df_merge = pd.merge(df_wynagrodzenia, wskaźnik_wartości_realnej, on='Rok')

    df_merge['Wynagrodzenie_realne'] = df_merge['Wartość'] / df_merge['Wartość_i'] * 100

    df_avg_nominalne = df_merge.groupby('Rok')['Wartość'].mean().reset_index()
    df_avg_realne = df_merge.groupby('Rok')['Wynagrodzenie_realne'].mean().reset_index()

    # Generowanie czwartego wykresu
    fig4, ax4 = plt.subplots()
    ax4.plot(df_avg_nominalne['Rok'], df_avg_nominalne['Wartość'], marker='', linestyle='-', label='Wynagrodzenie')
    ax4.plot(df_avg_realne['Rok'], df_avg_realne['Wynagrodzenie_realne'], marker='', linestyle='-',
             label='Wynagrodzenie realne')
    ax4.set_xlabel('Rok')
    ax4.set_ylabel('Wynagrodzenie brutto (zł)')
    ax4.legend()

    img4 = io.BytesIO()
    plt.savefig(img4, format='png')
    img4.seek(0)

    # Kodowanie obrazu w formacie base64
    plot_url4 = base64.b64encode(img4.getvalue()).decode()

    return render_template('analysis.html', plot_url1=plot_url1, plot_url2=plot_url2, plot_url3=plot_url3, plot_url4=plot_url4)

# Wylogowanie
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
