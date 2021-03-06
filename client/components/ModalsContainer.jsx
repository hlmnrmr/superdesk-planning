import React from 'react'
import { connect } from 'react-redux'
import { hideModal } from '../actions'
import {
    AgendaModal,
    ConfirmationModal,
    NotificationModal,
    EventActionConfirmationModal } from './index'

const modals = {
    CONFIRMATION: ConfirmationModal,
    CREATE_AGENDA: AgendaModal,
    EDIT_AGENDA: AgendaModal,
    NOTIFICATION_MODAL: NotificationModal,
    ITEM_ACTIONS_MODAL: EventActionConfirmationModal,
}

export function Modals({ modalType, modalProps, handleHide }) {
    if (modalType) {
        return React.createElement(modals[modalType], {
            handleHide,
            modalProps,
        })
    } else {
        return null
    }
}


Modals.propTypes = {
    modalType: React.PropTypes.string,
    modalProps: React.PropTypes.object,
    handleHide: React.PropTypes.func.isRequired,
}

const mapStateToProps = (state) => ({
    modalType: state.modal.modalType,
    modalProps: state.modal.modalProps,
})
const mapDispatchToProps = (dispatch) => ({ handleHide: () => dispatch(hideModal()) })
export const ModalsContainer = connect(mapStateToProps, mapDispatchToProps)(Modals)
