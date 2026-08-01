// Pas de console qui s'ouvre derriere l'application en release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    murmure_lib::run()
}
